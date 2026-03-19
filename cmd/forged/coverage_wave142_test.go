//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 142: protocol_validator.go uncovered branches
// Targets:
//   validateTaskCompletedMessage (75% → 100%)
//   validateTaskAssignedMessage  (83.3% → 100%)
//   validateHeartbeatMessage     (83.3% → 100%)
//   RunProtocolComplianceCheck   (84.6% → 100%)
// ---------------------------------------------------------------------------

// TestWave142_ValidateWSMessage_TaskCompleted_Valid covers the happy path
// for task.completed — both payload.task_id and payload.result present.
func TestWave142_ValidateWSMessage_TaskCompleted_Valid(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.completed","payload":{"task_id":"TASK-001","result":"success"},"timestamp":"2026-03-10T00:00:00Z"}`
	if err := v.ValidateWSMessage([]byte(data)); err != nil {
		t.Errorf("expected no error for valid task.completed, got: %v", err)
	}
}

// TestWave142_ValidateWSMessage_TaskCompleted_MissingTaskID covers missing
// payload.task_id in a task.completed message.
func TestWave142_ValidateWSMessage_TaskCompleted_MissingTaskID(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.completed","payload":{"result":"success"}}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for task.completed missing task_id")
	}
}

// TestWave142_ValidateWSMessage_TaskCompleted_MissingResult covers missing
// payload.result in a task.completed message.
func TestWave142_ValidateWSMessage_TaskCompleted_MissingResult(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.completed","payload":{"task_id":"TASK-001"}}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for task.completed missing result")
	}
}

// TestWave142_ValidateWSMessage_TaskCompleted_NonObjectPayload covers the
// invalid-payload path: payload is a string, not an object.
func TestWave142_ValidateWSMessage_TaskCompleted_NonObjectPayload(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.completed","payload":"not-an-object"}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for task.completed with non-object payload")
	}
}

// TestWave142_ValidateWSMessage_TaskAssigned_NonObjectPayload covers the
// invalid-payload path for task.assigned (triggers validateTaskAssignedMessage
// INVALID_PAYLOAD branch, previously uncovered at 83.3%).
func TestWave142_ValidateWSMessage_TaskAssigned_NonObjectPayload(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.assigned","payload":42}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for task.assigned with non-object payload")
	}
}

// TestWave142_ValidateWSMessage_Heartbeat_NonObjectPayload covers the
// invalid-payload path for heartbeat (triggers validateHeartbeatMessage
// INVALID_PAYLOAD branch, previously uncovered at 83.3%).
func TestWave142_ValidateWSMessage_Heartbeat_NonObjectPayload(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"heartbeat","payload":true}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for heartbeat with non-object payload")
	}
}

// TestWave142_RunProtocolComplianceCheck_NoSchemas covers the "no schemas loaded"
// branch in RunProtocolComplianceCheck. We create a validator with an empty
// schema map by using a bare struct literal.
func TestWave142_RunProtocolComplianceCheck_NoSchemas(t *testing.T) {
	v := &ProtocolValidator{schemas: map[string]Schema{}}
	result := v.RunProtocolComplianceCheck()
	if result.Valid {
		t.Error("expected compliance check to fail when no schemas loaded")
	}
	if len(result.Errors) == 0 {
		t.Error("expected at least one error when no schemas loaded")
	}
}

// TestWave142_RunProtocolComplianceCheck_MissingRequiredSchema covers the
// "missing required schema" branch: schemas map exists but lacks required keys.
func TestWave142_RunProtocolComplianceCheck_MissingRequiredSchema(t *testing.T) {
	// Provide only one schema — rest are missing → should report errors.
	v := &ProtocolValidator{schemas: map[string]Schema{
		"task_create": {},
	}}
	result := v.RunProtocolComplianceCheck()
	if result.Valid {
		t.Error("expected compliance check to fail when required schemas missing")
	}
}

// TestWave142_ValidateWSMessage_InvalidJSON covers the JSON unmarshal error
// path in ValidateWSMessage.
func TestWave142_ValidateWSMessage_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateWSMessage([]byte(`{bad json`))
	if err == nil {
		t.Fatal("expected error for invalid JSON in WS message")
	}
}

// TestWave142_ValidateTaskCompletedMessage_Direct exercises the private helper
// directly (via ValidateWSMessage dispatch) with an empty payload object to
// confirm task_id is enforced before result.
func TestWave142_ValidateTaskCompletedMessage_EmptyPayload(t *testing.T) {
	v := NewProtocolValidator()
	data := `{"type":"task.completed","payload":{}}`
	err := v.ValidateWSMessage([]byte(data))
	if err == nil {
		t.Fatal("expected error for task.completed with empty payload")
	}
}
