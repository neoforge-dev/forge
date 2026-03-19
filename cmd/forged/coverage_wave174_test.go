//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 174: protocol_validator.go pure functions + validator methods
// Targets:
//   ValidationError.Error()       (protocol_validator.go:23)
//   detectType                    (protocol_validator.go:503)
//   IsValidTaskID                 (protocol_validator.go:656)
//   IsValidAgentID                (protocol_validator.go:666)
//   GetProtocolVersion            (protocol_validator.go:651)
//   NewProtocolValidator          (protocol_validator.go:54)
//   isVersionCompatible           (protocol_validator.go:485)
//   ValidateTimestamp             (protocol_validator.go:391)
//   RunProtocolComplianceCheck    (protocol_validator.go:618)
//   ValidateTaskRequest           (protocol_validator.go:158)
//   ValidateTaskClaim             (protocol_validator.go:173)
//   ValidateApprovalRequest       (protocol_validator.go:188)
//   ValidateWSHandshake           (protocol_validator.go:203)
//   ValidateWSMessage             (protocol_validator.go:239)
//   ValidateResponse              (protocol_validator.go:571)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ValidationError.Error
// ---------------------------------------------------------------------------

func TestWave174_ValidationError_Error(t *testing.T) {
	e := ValidationError{
		Code:   "MISSING_FIELD",
		Field:  "id",
		Reason: "ID is required",
	}
	got := e.Error()
	if !strings.Contains(got, "MISSING_FIELD") || !strings.Contains(got, "id") {
		t.Errorf("unexpected Error() output: %q", got)
	}
}

// ---------------------------------------------------------------------------
// detectType
// ---------------------------------------------------------------------------

func TestWave174_DetectType_String(t *testing.T) {
	if detectType("hello") != "string" {
		t.Error("expected string")
	}
}

func TestWave174_DetectType_Integer(t *testing.T) {
	if detectType(float64(5)) != "integer" {
		t.Error("expected integer for whole-number float64")
	}
}

func TestWave174_DetectType_Number(t *testing.T) {
	if detectType(float64(3.14)) != "number" {
		t.Error("expected number for fractional float64")
	}
}

func TestWave174_DetectType_Bool(t *testing.T) {
	if detectType(true) != "boolean" {
		t.Error("expected boolean")
	}
}

func TestWave174_DetectType_Object(t *testing.T) {
	if detectType(map[string]interface{}{"a": 1}) != "object" {
		t.Error("expected object")
	}
}

func TestWave174_DetectType_Array(t *testing.T) {
	if detectType([]interface{}{1, 2}) != "array" {
		t.Error("expected array")
	}
}

func TestWave174_DetectType_Unknown(t *testing.T) {
	if detectType(nil) != "unknown" {
		t.Error("expected unknown for nil")
	}
}

// ---------------------------------------------------------------------------
// IsValidTaskID / IsValidAgentID
// ---------------------------------------------------------------------------

func TestWave174_IsValidTaskID_Valid(t *testing.T) {
	if !IsValidTaskID("TASK-123") {
		t.Error("expected valid task ID")
	}
}

func TestWave174_IsValidTaskID_Empty(t *testing.T) {
	if IsValidTaskID("") {
		t.Error("expected false for empty ID")
	}
}

func TestWave174_IsValidTaskID_Lowercase(t *testing.T) {
	if IsValidTaskID("task-123") {
		t.Error("expected false for lowercase task ID")
	}
}

func TestWave174_IsValidAgentID_Valid(t *testing.T) {
	if !IsValidAgentID("kimi-1") {
		t.Error("expected valid agent ID")
	}
}

func TestWave174_IsValidAgentID_Empty(t *testing.T) {
	if IsValidAgentID("") {
		t.Error("expected false for empty agent ID")
	}
}

func TestWave174_IsValidAgentID_Uppercase(t *testing.T) {
	if IsValidAgentID("Kimi") {
		t.Error("expected false for uppercase agent ID")
	}
}

// ---------------------------------------------------------------------------
// GetProtocolVersion
// ---------------------------------------------------------------------------

func TestWave174_GetProtocolVersion(t *testing.T) {
	v := GetProtocolVersion()
	if v == "" {
		t.Error("expected non-empty protocol version")
	}
	if !strings.Contains(v, ".") {
		t.Errorf("expected semver format, got %q", v)
	}
}

// ---------------------------------------------------------------------------
// NewProtocolValidator
// ---------------------------------------------------------------------------

func TestWave174_NewProtocolValidator_NonNil(t *testing.T) {
	v := NewProtocolValidator()
	if v == nil {
		t.Error("expected non-nil validator")
	}
}

func TestWave174_NewProtocolValidator_SchemasLoaded(t *testing.T) {
	v := NewProtocolValidator()
	if len(v.schemas) == 0 {
		t.Error("expected schemas to be loaded")
	}
}

// ---------------------------------------------------------------------------
// isVersionCompatible
// ---------------------------------------------------------------------------

func TestWave174_IsVersionCompatible_Same(t *testing.T) {
	v := NewProtocolValidator()
	if !v.isVersionCompatible("3.0.0", "3.0.0") {
		t.Error("expected compatible for same version")
	}
}

func TestWave174_IsVersionCompatible_MajorMismatch(t *testing.T) {
	v := NewProtocolValidator()
	if v.isVersionCompatible("2.0.0", "3.0.0") {
		t.Error("expected incompatible for different major version")
	}
}

func TestWave174_IsVersionCompatible_TooShort(t *testing.T) {
	v := NewProtocolValidator()
	if v.isVersionCompatible("3", "3.0.0") {
		t.Error("expected false for version without dot")
	}
}

// ---------------------------------------------------------------------------
// ValidateTimestamp
// ---------------------------------------------------------------------------

func TestWave174_ValidateTimestamp_RFC3339(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("2026-03-10T10:00:00Z"); err != nil {
		t.Errorf("expected valid RFC3339: %v", err)
	}
}

func TestWave174_ValidateTimestamp_Custom(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("2026-03-10T10:00:00"); err != nil {
		t.Errorf("expected valid custom format: %v", err)
	}
}

func TestWave174_ValidateTimestamp_Invalid(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("not-a-timestamp"); err == nil {
		t.Error("expected error for invalid timestamp")
	}
}

// ---------------------------------------------------------------------------
// RunProtocolComplianceCheck
// ---------------------------------------------------------------------------

func TestWave174_RunProtocolComplianceCheck_Valid(t *testing.T) {
	v := NewProtocolValidator()
	result := v.RunProtocolComplianceCheck()
	if !result.Valid {
		t.Errorf("expected valid compliance check, errors: %v", result.Errors)
	}
	if result.Version == "" {
		t.Error("expected non-empty version in compliance result")
	}
}

// ---------------------------------------------------------------------------
// ValidateTaskRequest
// ---------------------------------------------------------------------------

func TestWave174_ValidateTaskRequest_Valid(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"id":"TASK-1","type":"feature","priority":5}`)
	if err := v.ValidateTaskRequest(body); err != nil {
		t.Errorf("expected valid task request: %v", err)
	}
}

func TestWave174_ValidateTaskRequest_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTaskRequest([]byte("not-json")); err == nil {
		t.Error("expected error for invalid JSON")
	}
}

func TestWave174_ValidateTaskRequest_MissingID(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"feature"}`)
	if err := v.ValidateTaskRequest(body); err == nil {
		t.Error("expected error for missing id field")
	}
}

// ---------------------------------------------------------------------------
// ValidateTaskClaim
// ---------------------------------------------------------------------------

func TestWave174_ValidateTaskClaim_Valid(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"agent_id":"kimi-1"}`)
	if err := v.ValidateTaskClaim(body); err != nil {
		t.Errorf("expected valid claim: %v", err)
	}
}

func TestWave174_ValidateTaskClaim_MissingAgentID(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"force":true}`)
	if err := v.ValidateTaskClaim(body); err == nil {
		t.Error("expected error for missing agent_id")
	}
}

func TestWave174_ValidateTaskClaim_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTaskClaim([]byte("{")); err == nil {
		t.Error("expected error for invalid JSON")
	}
}

// ---------------------------------------------------------------------------
// ValidateApprovalRequest
// ---------------------------------------------------------------------------

func TestWave174_ValidateApprovalRequest_Valid(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"deployment","task_id":"TASK-1"}`)
	if err := v.ValidateApprovalRequest(body); err != nil {
		t.Errorf("expected valid approval: %v", err)
	}
}

func TestWave174_ValidateApprovalRequest_MissingType(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"task_id":"TASK-1"}`)
	if err := v.ValidateApprovalRequest(body); err == nil {
		t.Error("expected error for missing type")
	}
}

// ---------------------------------------------------------------------------
// ValidateWSHandshake
// ---------------------------------------------------------------------------

func TestWave174_ValidateWSHandshake_Valid(t *testing.T) {
	v := NewProtocolValidator()
	// Use map directly to bypass http.Header canonicalization
	headers := http.Header{"X-Worker-ID": []string{"kimi-1"}}
	if err := v.ValidateWSHandshake(headers); err != nil {
		t.Errorf("expected valid handshake: %v", err)
	}
}

func TestWave174_ValidateWSHandshake_MissingWorkerID(t *testing.T) {
	v := NewProtocolValidator()
	headers := http.Header{}
	if err := v.ValidateWSHandshake(headers); err == nil {
		t.Error("expected error for missing X-Worker-ID")
	}
}

func TestWave174_ValidateWSHandshake_IncompatibleVersion(t *testing.T) {
	v := NewProtocolValidator()
	// Use map directly to bypass http.Header canonicalization
	headers := http.Header{"X-Worker-ID": []string{"kimi-1"}, "X-Version": []string{"2.0.0"}}
	if err := v.ValidateWSHandshake(headers); err == nil {
		t.Error("expected error for incompatible version")
	}
}

// ---------------------------------------------------------------------------
// ValidateWSMessage
// ---------------------------------------------------------------------------

func TestWave174_ValidateWSMessage_Ping(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"ping","payload":{}}`)
	if err := v.ValidateWSMessage(body); err != nil {
		t.Errorf("expected valid ping: %v", err)
	}
}

func TestWave174_ValidateWSMessage_MissingType(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"payload":{}}`)
	if err := v.ValidateWSMessage(body); err == nil {
		t.Error("expected error for missing type")
	}
}

func TestWave174_ValidateWSMessage_InvalidTimestamp(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"ping","payload":{},"timestamp":"not-a-time"}`)
	if err := v.ValidateWSMessage(body); err == nil {
		t.Error("expected error for invalid timestamp")
	}
}

func TestWave174_ValidateWSMessage_TaskAssigned_MissingPayloadTaskID(t *testing.T) {
	v := NewProtocolValidator()
	// task.assigned requires payload.task_id
	body := []byte(`{"type":"task.assigned","payload":{}}`)
	if err := v.ValidateWSMessage(body); err == nil {
		t.Error("expected error for task.assigned missing task_id")
	}
}

func TestWave174_ValidateWSMessage_TaskCompleted_Valid(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"task.completed","payload":{"task_id":"T1","result":"done"}}`)
	if err := v.ValidateWSMessage(body); err != nil {
		t.Errorf("expected valid task.completed: %v", err)
	}
}

func TestWave174_ValidateWSMessage_Heartbeat_Valid(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"type":"heartbeat","payload":{"agent_id":"kimi-1"}}`)
	if err := v.ValidateWSMessage(body); err != nil {
		t.Errorf("expected valid heartbeat: %v", err)
	}
}

// ---------------------------------------------------------------------------
// ValidateResponse
// ---------------------------------------------------------------------------

func TestWave174_ValidateResponse_TasksEndpoint(t *testing.T) {
	v := NewProtocolValidator()
	body := []byte(`{"count":0,"tasks":[]}`)
	if err := v.ValidateResponse("/api/tasks", body); err != nil {
		t.Errorf("expected valid response: %v", err)
	}
}

func TestWave174_ValidateResponse_UnknownEndpoint(t *testing.T) {
	v := NewProtocolValidator()
	// Unknown endpoints skip validation → nil
	if err := v.ValidateResponse("/unknown", []byte(`{"x":1}`)); err != nil {
		t.Errorf("expected nil for unknown endpoint: %v", err)
	}
}
