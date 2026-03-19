//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 218: approval_integration.go pure functions
// Targets:
//   NewTaskApprovalIntegration   (approval_integration.go:20) — non-nil
//   CalculateConfidenceFromInput (approval_integration.go:173)— all branches
//   ApprovalRequiredError.Error  (approval_integration.go:201)
//   mustJSON                     (approval_integration.go:271)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewTaskApprovalIntegration
// ---------------------------------------------------------------------------

func TestWave218_NewTaskApprovalIntegration_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, _ := NewTaskQueueFromDB(db)
	as := NewApprovalService(NewApprovalStore(db))
	tai := NewTaskApprovalIntegration(tq, as)
	if tai == nil {
		t.Error("expected non-nil TaskApprovalIntegration")
	}
}

func TestWave218_NewTaskApprovalIntegration_NilArgs(t *testing.T) {
	tai := NewTaskApprovalIntegration(nil, nil)
	if tai == nil {
		t.Error("expected non-nil TaskApprovalIntegration with nil args")
	}
}

// ---------------------------------------------------------------------------
// CalculateConfidenceFromInput
// ---------------------------------------------------------------------------

func TestWave218_CalculateConfidenceFromInput_HighConfidence(t *testing.T) {
	input := ConfidenceFactorsInput{
		TestsPassed:  10,
		TestsFailed:  0,
		TestCoverage: 90.0,
		PatternMatch: 1.0,
		BlastRadius:  1,
		Reversible:   true,
	}
	result := CalculateConfidenceFromInput(input)
	if result.Score < 0 || result.Score > 1 {
		t.Errorf("expected score in [0, 1], got %f", result.Score)
	}
}

func TestWave218_CalculateConfidenceFromInput_LowConfidence(t *testing.T) {
	input := ConfidenceFactorsInput{
		TestsPassed:  0,
		TestsFailed:  5,
		TestCoverage: 0.0,
		PatternMatch: 0.0,
		BlastRadius:  10,
		Reversible:   false,
	}
	result := CalculateConfidenceFromInput(input)
	if result.Score < 0 || result.Score > 1 {
		t.Errorf("expected score in [0, 1], got %f", result.Score)
	}
}

func TestWave218_CalculateConfidenceFromInput_ZeroAll(t *testing.T) {
	result := CalculateConfidenceFromInput(ConfidenceFactorsInput{})
	if result.Score < 0 || result.Score > 1 {
		t.Errorf("expected score in [0, 1] for zero input, got %f", result.Score)
	}
}

// ---------------------------------------------------------------------------
// ApprovalRequiredError.Error
// ---------------------------------------------------------------------------

func TestWave218_ApprovalRequiredError_Error(t *testing.T) {
	e := &ApprovalRequiredError{
		TaskID:     "task-1",
		ApprovalID: "approval-1",
		Confidence: 0.4,
		Reason:     "low confidence",
	}
	got := e.Error()
	if got == "" {
		t.Error("expected non-empty error string")
	}
	if len(got) < len("task task-1") {
		t.Errorf("error too short: %q", got)
	}
}

// ---------------------------------------------------------------------------
// mustJSON
// ---------------------------------------------------------------------------

func TestWave218_MustJSON_Map(t *testing.T) {
	got := mustJSON(map[string]string{"key": "value"})
	if len(got) == 0 {
		t.Error("expected non-empty JSON")
	}
}

func TestWave218_MustJSON_Nil(t *testing.T) {
	got := mustJSON(nil)
	_ = got // nil → "null" or empty — just no panic
}
