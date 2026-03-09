//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave9_test.go — Coverage wave 9: protocol_validator.go, errors.go, quality_gates.go
// Target: +2pp from ~57% to ~59%
package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- protocol_validator.go: ValidationError, GetProtocolVersion ---

func TestValidationError_Error(t *testing.T) {
	ve := ValidationError{
		Field:  "agent_id",
		Reason: "must not be empty",
		Code:   "MISSING_FIELD",
	}
	msg := ve.Error()
	if msg == "" {
		t.Error("ValidationError.Error() should return non-empty string")
	}
	if ve.Code != "MISSING_FIELD" {
		t.Errorf("code mismatch: %s", ve.Code)
	}
}

func TestGetProtocolVersion(t *testing.T) {
	v := GetProtocolVersion()
	if v == "" {
		t.Error("GetProtocolVersion should return non-empty string")
	}
	if v != ProtocolVersion {
		t.Errorf("expected %s, got %s", ProtocolVersion, v)
	}
}

// --- ValidateTaskClaim ---

func TestValidateTaskClaim_Valid(t *testing.T) {
	v := NewProtocolValidator()
	data, _ := json.Marshal(map[string]string{"agent_id": "agent-001"})
	err := v.ValidateTaskClaim(data)
	if err != nil {
		t.Errorf("ValidateTaskClaim valid: unexpected error: %v", err)
	}
}

func TestValidateTaskClaim_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateTaskClaim([]byte("{bad json"))
	if err == nil {
		t.Error("expected error for invalid JSON")
	}
}

func TestValidateTaskClaim_MissingAgentID(t *testing.T) {
	v := NewProtocolValidator()
	data, _ := json.Marshal(map[string]string{"other_field": "value"})
	err := v.ValidateTaskClaim(data)
	if err == nil {
		t.Error("expected error for missing agent_id")
	}
}

// --- validateTaskCompletedMessage (via ValidateWSMessage with type=task.completed) ---

func TestValidateWSMessage_TaskCompleted_Valid(t *testing.T) {
	v := NewProtocolValidator()
	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "task.completed",
		"agent_id":   "agent-001",
		"message_id": "msg-001",
		"timestamp":  "2026-03-07T10:00:00Z",
		"payload": map[string]interface{}{
			"task_id": "TASK-001",
			"result":  "success",
		},
	})
	err := v.ValidateWSMessage(msg)
	if err != nil {
		t.Errorf("ValidateWSMessage task.completed valid: %v", err)
	}
}

func TestValidateWSMessage_TaskCompleted_MissingPayload(t *testing.T) {
	v := NewProtocolValidator()
	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "task.completed",
		"agent_id":   "agent-001",
		"message_id": "msg-001",
		"timestamp":  "2026-03-07T10:00:00Z",
		"payload":    "not_an_object",
	})
	err := v.ValidateWSMessage(msg)
	if err == nil {
		t.Error("expected error for non-object payload")
	}
}

func TestValidateWSMessage_TaskCompleted_MissingTaskID(t *testing.T) {
	v := NewProtocolValidator()
	msg, _ := json.Marshal(map[string]interface{}{
		"type":       "task.completed",
		"agent_id":   "agent-001",
		"message_id": "msg-001",
		"timestamp":  "2026-03-07T10:00:00Z",
		"payload": map[string]interface{}{
			"result": "success",
		},
	})
	err := v.ValidateWSMessage(msg)
	if err == nil {
		t.Error("expected error for missing task_id in payload")
	}
}

// --- ValidationMiddleware ---

func TestValidationMiddleware_GET_PassThrough(t *testing.T) {
	v := NewProtocolValidator()
	handler := v.ValidationMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("GET should pass through middleware, got %d", w.Code)
	}
}

func TestValidationMiddleware_POST_Unknown(t *testing.T) {
	v := NewProtocolValidator()
	handler := v.ValidationMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/unknown", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	// Unknown endpoint passes through
	if w.Code != http.StatusOK {
		t.Errorf("unknown endpoint should pass through, got %d", w.Code)
	}
}

// --- ValidateResponse ---

func TestValidateResponse_UnknownEndpoint(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateResponse("/api/unknown", []byte(`{"key":"value"}`))
	if err != nil {
		t.Errorf("unknown endpoint should return nil: %v", err)
	}
}

func TestValidateResponse_TasksEndpoint_Valid(t *testing.T) {
	v := NewProtocolValidator()
	data, _ := json.Marshal(map[string]interface{}{
		"tasks": []interface{}{},
		"count": 0,
	})
	err := v.ValidateResponse("/api/tasks", data)
	if err != nil {
		t.Errorf("ValidateResponse tasks valid: %v", err)
	}
}

func TestValidateResponse_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateResponse("/api/tasks", []byte("{invalid"))
	if err == nil {
		t.Error("expected error for invalid JSON response")
	}
}

func TestValidateResponse_ErrorResponse(t *testing.T) {
	v := NewProtocolValidator()
	data, _ := json.Marshal(map[string]string{"error": "not found"})
	err := v.ValidateResponse("/api/tasks", data)
	if err != nil {
		t.Errorf("error response with string error field should be valid: %v", err)
	}
}

// --- RunProtocolComplianceCheck ---

func TestRunProtocolComplianceCheck(t *testing.T) {
	v := NewProtocolValidator()
	result := v.RunProtocolComplianceCheck()
	if !result.Valid {
		t.Errorf("compliance check should be valid: errors=%v", result.Errors)
	}
	if result.Version == "" {
		t.Error("compliance check should have non-empty version")
	}
}

// --- detectType ---

func TestDetectType_Values(t *testing.T) {
	tests := []struct {
		val  interface{}
		want string
	}{
		{"hello", "string"},
		{float64(3), "integer"},
		{float64(3.5), "number"},
		{true, "boolean"},
		{map[string]interface{}{}, "object"},
		{[]interface{}{}, "array"},
		{nil, "unknown"},
	}
	for _, tc := range tests {
		got := detectType(tc.val)
		if got != tc.want {
			t.Errorf("detectType(%v) = %q, want %q", tc.val, got, tc.want)
		}
	}
}

// --- quality_gates.go: NewQualityGateChecker, CheckCoverage stub ---

func TestNewQualityGateChecker_NoConfig(t *testing.T) {
	qg, err := NewQualityGateChecker("")
	if err != nil {
		t.Fatalf("NewQualityGateChecker: %v", err)
	}
	if qg == nil {
		t.Fatal("expected non-nil checker")
	}
}

func TestNewQualityGateChecker_NonExistentConfig(t *testing.T) {
	// Non-existent config file should use defaults without error
	qg, err := NewQualityGateChecker("/tmp/nonexistent-qg-config.yaml")
	if err != nil {
		t.Fatalf("expected no error for missing config file: %v", err)
	}
	if qg == nil {
		t.Fatal("expected non-nil checker")
	}
}
