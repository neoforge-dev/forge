//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 198: gate_executor.go
// Targets:
//   CommandGateExecutor.ExecuteGate (gate_executor.go:42) — success / fail / expected-not-found
//   NewHTTPGateExecutor             (gate_executor.go:82) — non-nil
//   HTTPGateExecutor.ExecuteGate    (gate_executor.go:88) — bad URL
//   CheckpointGateExecutor.ExecuteGate(gate_executor.go:130) — unknown type, count:N with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// CommandGateExecutor
// ---------------------------------------------------------------------------

func TestWave198_CommandGateExecutor_Success(t *testing.T) {
	e := &CommandGateExecutor{}
	gate := QualityGate{
		ID:      "wave198-cmd",
		Type:    "command",
		Command: "echo hello",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected passed=true, got false: %s", result.Error)
	}
}

func TestWave198_CommandGateExecutor_Fail(t *testing.T) {
	e := &CommandGateExecutor{}
	gate := QualityGate{
		ID:      "wave198-fail",
		Type:    "command",
		Command: "exit 1",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false for exit 1")
	}
}

func TestWave198_CommandGateExecutor_ExpectedNotFound(t *testing.T) {
	e := &CommandGateExecutor{}
	gate := QualityGate{
		ID:       "wave198-expected",
		Type:     "command",
		Command:  "echo hello",
		Expected: "NOT_PRESENT",
		Timeout:  5 * time.Second,
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false when expected string not found")
	}
}

func TestWave198_CommandGateExecutor_ExpectedFound(t *testing.T) {
	e := &CommandGateExecutor{}
	gate := QualityGate{
		ID:       "wave198-expected-ok",
		Type:     "command",
		Command:  "echo hello world",
		Expected: "hello",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected passed=true when expected found, got false: %s", result.Error)
	}
}

// ---------------------------------------------------------------------------
// NewHTTPGateExecutor
// ---------------------------------------------------------------------------

func TestWave198_NewHTTPGateExecutor_NotNil(t *testing.T) {
	e := NewHTTPGateExecutor()
	if e == nil {
		t.Error("expected non-nil HTTPGateExecutor")
	}
	if e.Client == nil {
		t.Error("expected non-nil HTTP client")
	}
}

// ---------------------------------------------------------------------------
// HTTPGateExecutor
// ---------------------------------------------------------------------------

func TestWave198_HTTPGateExecutor_BadURL(t *testing.T) {
	e := NewHTTPGateExecutor()
	gate := QualityGate{
		ID:   "wave198-http-bad",
		Type: "http",
		URL:  "://invalid-url",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false for bad URL")
	}
}

// ---------------------------------------------------------------------------
// CheckpointGateExecutor
// ---------------------------------------------------------------------------

func TestWave198_CheckpointGateExecutor_UnknownType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	e := &CheckpointGateExecutor{DB: db}
	gate := QualityGate{
		ID:       "wave198-checkpoint",
		Type:     "checkpoint",
		Expected: "unknown:type",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if result.Passed {
		t.Error("expected passed=false for unknown checkpoint type")
	}
}

func TestWave198_CheckpointGateExecutor_CountSuccess(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	e := &CheckpointGateExecutor{DB: db}
	gate := QualityGate{
		ID:       "wave198-count",
		Type:     "checkpoint",
		Command:  "SELECT COUNT(*) FROM tasks",
		Expected: "count:0",
	}
	result, err := e.ExecuteGate(context.Background(), gate)
	if err != nil {
		t.Fatalf("ExecuteGate: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected passed=true for count:0, got false: %s", result.Error)
	}
}
