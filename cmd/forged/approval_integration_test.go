//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
)

func TestCalculateConfidenceFromInput(t *testing.T) {
	tests := []struct {
		name      string
		input     ConfidenceFactorsInput
		wantHigh  bool // expect score >= 0.7
	}{
		{
			name: "high confidence — all tests pass, good coverage",
			input: ConfidenceFactorsInput{
				TestsPassed:  20,
				TestsFailed:  0,
				TestsSkipped: 0,
				TestCoverage: 85.0,
				PatternMatch: 0.9,
				BlastRadius:  2,
				Reversible:   true,
			},
			wantHigh: true,
		},
		{
			name: "low confidence — tests failing",
			input: ConfidenceFactorsInput{
				TestsPassed:  5,
				TestsFailed:  10,
				TestsSkipped: 0,
				TestCoverage: 30.0,
				PatternMatch: 0.3,
				BlastRadius:  25,
				Reversible:   false,
			},
			wantHigh: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := CalculateConfidenceFromInput(tt.input)
			if tt.wantHigh && result.Score < 0.5 {
				t.Errorf("expected high confidence score, got %.2f", result.Score)
			}
			if !tt.wantHigh && result.Score > 0.9 {
				t.Errorf("expected low confidence score, got %.2f", result.Score)
			}
		})
	}
}

func TestMustJSON(t *testing.T) {
	data := mustJSON(map[string]string{"key": "value"})
	if data == nil {
		t.Fatal("expected non-nil JSON output")
	}
	if string(data) != `{"key":"value"}` {
		t.Errorf("unexpected JSON: %s", data)
	}
	// Unmarshalable type returns nil
	ch := make(chan int)
	if mustJSON(ch) != nil {
		t.Error("expected nil for unmarshalable type")
	}
}

func TestApprovalRequiredError(t *testing.T) {
	err := &ApprovalRequiredError{
		TaskID:     "task-123",
		ApprovalID: "appr-456",
		Confidence: 0.65,
		Reason:     "blast radius too high",
	}
	msg := err.Error()
	if !strings.Contains(msg, "task-123") {
		t.Errorf("error message missing task ID: %s", msg)
	}
	if !strings.Contains(msg, "appr-456") {
		t.Errorf("error message missing approval ID: %s", msg)
	}
	if !strings.Contains(msg, "65%") {
		t.Errorf("error message missing confidence percentage: %s", msg)
	}
}
