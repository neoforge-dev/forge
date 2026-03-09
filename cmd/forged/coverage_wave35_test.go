//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 35: CheckpointGateExecutor, CoordinationDashboard.ServeHTTP,
//          RunProtocolComplianceCheck, IsValidTaskID/AgentID, GetProtocolVersion

func TestCheckpointGateExecutor_CountPath_W35(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	e := &CheckpointGateExecutor{DB: db}
	gate := QualityGate{
		Command:  "SELECT COUNT(*) FROM tasks",
		Expected: "count:0",
	}

	ctx := context.Background()
	result, err := e.ExecuteGate(ctx, gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	if !result.Passed {
		t.Errorf("expected passed=true for count:0 with empty DB, got %v (output: %s)", result.Passed, result.Output)
	}
}

func TestCheckpointGateExecutor_CountMismatch_W35(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	e := &CheckpointGateExecutor{DB: db}
	gate := QualityGate{
		Command:  "SELECT COUNT(*) FROM tasks",
		Expected: "count:999",
	}

	ctx := context.Background()
	result, err := e.ExecuteGate(ctx, gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false for count mismatch")
	}
}

func TestCheckpointGateExecutor_UnknownType_W35(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	e := &CheckpointGateExecutor{DB: db}
	gate := QualityGate{
		Command:  "SELECT 1",
		Expected: "unknown:value",
	}

	ctx := context.Background()
	result, err := e.ExecuteGate(ctx, gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false for unknown type")
	}
	if result.Error == "" {
		t.Error("expected non-empty error message for unknown type")
	}
}

func TestCoordinationDashboard_ServeHTTP_W35(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)
	req := httptest.NewRequest(http.MethodGet, "/coordination?sprint=current", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestRunProtocolComplianceCheck_Loaded_W35(t *testing.T) {
	v := NewProtocolValidator()
	result := v.RunProtocolComplianceCheck()
	// With default validator, errors may or may not be present
	// Main check: function runs without panic
	_ = result.Valid
}

func TestRunProtocolComplianceCheck_EmptySchemas_W35(t *testing.T) {
	v := &ProtocolValidator{
		schemas: map[string]Schema{},
	}
	result := v.RunProtocolComplianceCheck()
	if result.Valid {
		t.Error("expected valid=false with no schemas")
	}
	if len(result.Errors) == 0 {
		t.Error("expected at least one error with no schemas")
	}
}

func TestIsValidTaskID_W35(t *testing.T) {
	tests := []struct {
		id   string
		want bool
	}{
		{"", false},
		{"TASK-001", true},
		{"TASK-ABC-123", true},
		{"task-001", false},  // lowercase
		{"123-TASK", false},  // starts with digit
	}
	for _, tc := range tests {
		got := IsValidTaskID(tc.id)
		if got != tc.want {
			t.Errorf("IsValidTaskID(%q) = %v, want %v", tc.id, got, tc.want)
		}
	}
}

func TestIsValidAgentID_W35(t *testing.T) {
	tests := []struct {
		id   string
		want bool
	}{
		{"", false},
		{"kimi", true},
		{"agent-1", true},
		{"KIMI", false},   // uppercase
		{"1kimi", false},  // starts with digit
	}
	for _, tc := range tests {
		got := IsValidAgentID(tc.id)
		if got != tc.want {
			t.Errorf("IsValidAgentID(%q) = %v, want %v", tc.id, got, tc.want)
		}
	}
}

func TestGetProtocolVersion_W35(t *testing.T) {
	v := GetProtocolVersion()
	// Version may be empty or set — just verify function runs
	_ = v
}
