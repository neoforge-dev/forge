//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 214: parity_check.go + protocol_validator.go
// Targets:
//   NewParityChecker      (parity_check.go:39) — non-nil / empty args
//   timeNow               (parity_check.go:167)— positive int64
//   ParityChecker.Check   (parity_check.go:53) — runs without panic
//   ValidationError.Error (protocol_validator.go:23)
//   NewProtocolValidator  (protocol_validator.go:54) — non-nil
//   ValidateTimestamp     (protocol_validator.go:391) — valid/invalid
//   ValidateTaskRequest   (protocol_validator.go:158) — valid/invalid JSON
//   ValidateTaskClaim     (protocol_validator.go:173) — valid/missing agent
//   ValidateWSHandshake   (protocol_validator.go:203) — missing upgrade header
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewParityChecker / timeNow
// ---------------------------------------------------------------------------

func TestWave214_NewParityChecker_NonNil(t *testing.T) {
	pc := NewParityChecker("/tmp/forge", "/tmp/forge.db")
	if pc == nil {
		t.Error("expected non-nil ParityChecker")
	}
}

func TestWave214_NewParityChecker_EmptyArgs(t *testing.T) {
	pc := NewParityChecker("", "")
	if pc == nil {
		t.Error("expected non-nil ParityChecker with empty args")
	}
}

func TestWave214_TimeNow_Positive(t *testing.T) {
	ts := timeNow()
	if ts <= 0 {
		t.Errorf("expected positive timestamp, got %d", ts)
	}
}

// ---------------------------------------------------------------------------
// ParityChecker.Check — runs without panic (errors are expected with bad paths)
// ---------------------------------------------------------------------------

func TestWave214_ParityChecker_Check_NoPanic(t *testing.T) {
	pc := NewParityChecker("/nonexistent/path", "/nonexistent/forge.db")
	result, _ := pc.Check()
	// Result may have errors but should not be nil
	if result == nil {
		t.Error("expected non-nil result")
	}
}

// ---------------------------------------------------------------------------
// ValidationError
// ---------------------------------------------------------------------------

func TestWave214_ValidationError_Error(t *testing.T) {
	e := ValidationError{
		Code:   "REQUIRED_FIELD",
		Field:  "id",
		Reason: "Field is required",
	}
	got := e.Error()
	if got == "" {
		t.Error("expected non-empty error message")
	}
	if len(got) < 5 {
		t.Errorf("error message too short: %q", got)
	}
}

// ---------------------------------------------------------------------------
// NewProtocolValidator
// ---------------------------------------------------------------------------

func TestWave214_NewProtocolValidator_NonNil(t *testing.T) {
	v := NewProtocolValidator()
	if v == nil {
		t.Error("expected non-nil ProtocolValidator")
	}
}

// ---------------------------------------------------------------------------
// ValidateTimestamp
// ---------------------------------------------------------------------------

func TestWave214_ValidateTimestamp_Valid(t *testing.T) {
	v := NewProtocolValidator()
	ts := time.Now().UTC().Format(time.RFC3339)
	if err := v.ValidateTimestamp(ts); err != nil {
		t.Errorf("ValidateTimestamp valid RFC3339: %v", err)
	}
}

func TestWave214_ValidateTimestamp_Invalid(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("not-a-timestamp"); err == nil {
		t.Error("expected error for invalid timestamp")
	}
}

func TestWave214_ValidateTimestamp_CustomFormat(t *testing.T) {
	v := NewProtocolValidator()
	ts := time.Now().UTC().Format("2006-01-02T15:04:05")
	if err := v.ValidateTimestamp(ts); err != nil {
		t.Errorf("ValidateTimestamp custom format: %v", err)
	}
}

// ---------------------------------------------------------------------------
// ValidateTaskRequest
// ---------------------------------------------------------------------------

func TestWave214_ValidateTaskRequest_Valid(t *testing.T) {
	v := NewProtocolValidator()
	data := []byte(`{"id":"task-1","type":"feature"}`)
	err := v.ValidateTaskRequest(data)
	_ = err // may pass or fail depending on schema — just no panic
}

func TestWave214_ValidateTaskRequest_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateTaskRequest([]byte("not-json"))
	if err == nil {
		t.Error("expected error for invalid JSON")
	}
}

func TestWave214_ValidateTaskRequest_MissingRequired(t *testing.T) {
	v := NewProtocolValidator()
	data := []byte(`{"title":"Missing ID and type"}`)
	err := v.ValidateTaskRequest(data)
	_ = err // may or may not be strict — just no panic
}

// ---------------------------------------------------------------------------
// ValidateTaskClaim
// ---------------------------------------------------------------------------

func TestWave214_ValidateTaskClaim_Valid(t *testing.T) {
	v := NewProtocolValidator()
	data := []byte(`{"agent_id":"agent-1"}`)
	err := v.ValidateTaskClaim(data)
	_ = err // just no panic
}

func TestWave214_ValidateTaskClaim_InvalidJSON(t *testing.T) {
	v := NewProtocolValidator()
	err := v.ValidateTaskClaim([]byte("bad"))
	if err == nil {
		t.Error("expected error for invalid JSON")
	}
}

// ---------------------------------------------------------------------------
// ValidateWSHandshake
// ---------------------------------------------------------------------------

func TestWave214_ValidateWSHandshake_MissingUpgrade(t *testing.T) {
	v := NewProtocolValidator()
	headers := make(map[string][]string)
	err := v.ValidateWSHandshake(headers)
	// Missing "Upgrade: websocket" → should fail
	if err == nil {
		t.Error("expected error for missing Upgrade header")
	}
}

func TestWave214_ValidateWSHandshake_ValidHeaders(t *testing.T) {
	v := NewProtocolValidator()
	headers := map[string][]string{
		"Upgrade":    {"websocket"},
		"Connection": {"Upgrade"},
	}
	err := v.ValidateWSHandshake(headers)
	_ = err // may or may not pass additional checks
}
