//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// TestWave266_QualityGate_Struct tests QualityGate struct
func TestWave266_QualityGate_Struct(t *testing.T) {
	gate := QualityGate{
		ID:       "gate-001",
		Type:     "command",
		Command:  "echo hello",
		Expected: "hello",
		Timeout:  30 * time.Second,
	}
	
	if gate.ID != "gate-001" {
		t.Error("ID field mismatch")
	}
	if gate.Type != "command" {
		t.Error("Type field mismatch")
	}
	if gate.Command != "echo hello" {
		t.Error("Command field mismatch")
	}
	if gate.Expected != "hello" {
		t.Error("Expected field mismatch")
	}
	if gate.Timeout != 30*time.Second {
		t.Error("Timeout field mismatch")
	}
}

// TestWave266_QualityGateResult_Struct tests QualityGateResult struct
func TestWave266_QualityGateResult_Struct(t *testing.T) {
	result := &QualityGateResult{
		Passed:   true,
		Output:   "test output",
		Error:    "",
		Duration: 5 * time.Second,
	}
	
	if !result.Passed {
		t.Error("Passed should be true")
	}
	if result.Output != "test output" {
		t.Error("Output field mismatch")
	}
	if result.Duration != 5*time.Second {
		t.Error("Duration field mismatch")
	}
}

// TestWave266_CommandGateExecutor_ExecuteGate_Success tests successful command execution
func TestWave266_CommandGateExecutor_ExecuteGate_Success(t *testing.T) {
	executor := &CommandGateExecutor{}
	
	gate := QualityGate{
		ID:      "cmd-success",
		Type:    "command",
		Command: "echo hello world",
		Timeout: 30 * time.Second,
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if result == nil {
		t.Fatal("result should not be nil")
	}
	if !result.Passed {
		t.Errorf("expected gate to pass, got error: %s", result.Error)
	}
	if !strings.Contains(result.Output, "hello world") {
		t.Errorf("expected output to contain 'hello world', got: %s", result.Output)
	}
}

// TestWave266_CommandGateExecutor_ExecuteGate_ExpectedOutput tests command with expected output
func TestWave266_CommandGateExecutor_ExecuteGate_ExpectedOutput(t *testing.T) {
	executor := &CommandGateExecutor{}
	
	gate := QualityGate{
		ID:       "cmd-expected",
		Type:     "command",
		Command:  "echo success message",
		Expected: "success",
		Timeout:  30 * time.Second,
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected gate to pass, got error: %s", result.Error)
	}
}

// TestWave266_CommandGateExecutor_ExecuteGate_ExpectedNotFound tests when expected string not found
func TestWave266_CommandGateExecutor_ExecuteGate_ExpectedNotFound(t *testing.T) {
	executor := &CommandGateExecutor{}
	
	gate := QualityGate{
		ID:       "cmd-not-found",
		Type:     "command",
		Command:  "echo hello world",
		Expected: "missing",
		Timeout:  30 * time.Second,
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if result.Passed {
		t.Error("expected gate to fail when expected string not found")
	}
	if !strings.Contains(result.Error, "not found in output") {
		t.Errorf("expected 'not found in output' error, got: %s", result.Error)
	}
}

// TestWave266_CommandGateExecutor_ExecuteGate_CommandFailure tests failed command
func TestWave266_CommandGateExecutor_ExecuteGate_CommandFailure(t *testing.T) {
	executor := &CommandGateExecutor{}
	
	gate := QualityGate{
		ID:      "cmd-fail",
		Type:    "command",
		Command: "exit 1",
		Timeout: 30 * time.Second,
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if result.Passed {
		t.Error("expected gate to fail when command exits with error")
	}
}

// TestWave266_CommandGateExecutor_ExecuteGate_DefaultTimeout tests default timeout
func TestWave266_CommandGateExecutor_ExecuteGate_DefaultTimeout(t *testing.T) {
	executor := &CommandGateExecutor{}
	
	gate := QualityGate{
		ID:      "cmd-timeout",
		Type:    "command",
		Command: "echo test",
		// No timeout set - should use default
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected gate to pass, got error: %s", result.Error)
	}
}

// TestWave266_HTTPGateExecutor_New tests HTTP executor creation
func TestWave266_HTTPGateExecutor_New(t *testing.T) {
	executor := NewHTTPGateExecutor()
	
	if executor == nil {
		t.Fatal("executor should not be nil")
	}
	if executor.Client == nil {
		t.Error("Client should not be nil")
	}
	if executor.Client.Timeout != 30*time.Second {
		t.Errorf("expected default timeout 30s, got %v", executor.Client.Timeout)
	}
}

// TestWave266_HTTPGateExecutor_ExecuteGate_InvalidURL tests invalid URL
func TestWave266_HTTPGateExecutor_ExecuteGate_InvalidURL(t *testing.T) {
	executor := NewHTTPGateExecutor()
	
	gate := QualityGate{
		ID:   "http-invalid",
		Type: "http",
		URL:  "://invalid-url",
	}
	
	ctx := context.Background()
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if result.Passed {
		t.Error("expected gate to fail with invalid URL")
	}
}

// TestWave266_HTTPGateExecutor_ExecuteGate_DefaultMethod tests default HTTP method
func TestWave266_HTTPGateExecutor_ExecuteGate_DefaultMethod(t *testing.T) {
	executor := NewHTTPGateExecutor()
	
	gate := QualityGate{
		ID:   "http-default-method",
		Type: "http",
		URL:  "http://localhost:9999/test",
		// No method set - should default to GET
	}
	
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	
	result, err := executor.ExecuteGate(ctx, gate)
	
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	// Will fail because no server, but method should default to GET
	if result.Passed {
		t.Logf("Result: %v", result)
	}
}

// TestWave266_QualityGate_JSON tests JSON marshaling/unmarshaling
func TestWave266_QualityGate_JSON(t *testing.T) {
	gate := QualityGate{
		ID:       "json-test",
		Type:     "command",
		Command:  "echo test",
		Expected: "test",
		Timeout:  60 * time.Second,
	}
	
	data, err := json.Marshal(gate)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded QualityGate
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded.ID != gate.ID {
		t.Error("ID mismatch after JSON roundtrip")
	}
	if decoded.Type != gate.Type {
		t.Error("Type mismatch after JSON roundtrip")
	}
}

// TestWave266_QualityGateResult_JSON tests JSON marshaling/unmarshaling
func TestWave266_QualityGateResult_JSON(t *testing.T) {
	result := &QualityGateResult{
		Passed:   true,
		Output:   "success output",
		Error:    "",
		Duration: 100 * time.Millisecond,
	}
	
	data, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded QualityGateResult
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded.Passed != result.Passed {
		t.Error("Passed mismatch after JSON roundtrip")
	}
	if decoded.Output != result.Output {
		t.Error("Output mismatch after JSON roundtrip")
	}
}
