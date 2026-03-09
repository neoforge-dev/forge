//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"
)

type MockHook struct {
	Called bool
	Event  HookEvent
}

func (m *MockHook) Name() string {
	return "mock_hook"
}

func (m *MockHook) Trigger(ctx context.Context, event HookEvent) error {
	m.Called = true
	m.Event = event
	return nil
}

func TestRoyalJellyHooks(t *testing.T) {
	rj := NewRoyalJelly(nil, nil)

	mockHook := &MockHook{}
	rj.RegisterHook("context_threshold", mockHook)

	event := HookEvent{
		Type:       "context_threshold",
		AgentID:    "test-agent",
		ContextPct: 55.0,
	}

	err := rj.TriggerHooks(context.Background(), event)
	if err != nil {
		t.Fatalf("Expected no error, got %v", err)
	}

	if !mockHook.Called {
		t.Errorf("Expected hook to be called, but it wasn't")
	}

	if mockHook.Event.AgentID != "test-agent" {
		t.Errorf("Expected agent ID test-agent, got %s", mockHook.Event.AgentID)
	}
}

func TestRoyalJelly_TriggerUnknownEvent(t *testing.T) {
	rj := NewRoyalJelly(nil, nil)
	err := rj.TriggerHooks(context.Background(), HookEvent{Type: "no-such-event"})
	if err != nil {
		t.Errorf("expected nil for unknown event, got: %v", err)
	}
}

func TestRoyalJelly_HookErrorContinues(t *testing.T) {
	rj := NewRoyalJelly(nil, nil)
	secondRan := false

	rj.RegisterHook("evt", &errorHook{})
	rj.RegisterHook("evt", &callbackHook{fn: func() { secondRan = true }})

	rj.TriggerHooks(context.Background(), HookEvent{Type: "evt"})
	if !secondRan {
		t.Error("expected second hook to run despite first failing")
	}
}

func TestContextThresholdHook_Name_Extended(t *testing.T) {
	h := &ContextThresholdHook{cm: nil}
	if h.Name() != "context_threshold_envelope" {
		t.Errorf("unexpected hook name: %q", h.Name())
	}
}

func TestContextThresholdHook_BelowThreshold(t *testing.T) {
	h := &ContextThresholdHook{cm: nil}
	event := HookEvent{
		Type:       "context_threshold",
		AgentID:    "agent-1",
		ContextPct: 30.0,
		Metadata:   map[string]interface{}{},
	}
	err := h.Trigger(context.Background(), event)
	if err != nil {
		t.Errorf("expected no error below threshold, got: %v", err)
	}
}

func TestContextThresholdHook_AboveThreshold_WithDB(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_rj_thresh_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)
	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	cm := NewContextManager(db, t.TempDir())
	defer cm.Stop()
	h := &ContextThresholdHook{cm: cm}
	event := HookEvent{
		Type:       "context_threshold",
		AgentID:    "agent-2",
		ContextPct: 55.0,
		Metadata: map[string]interface{}{
			"domain":  "forge",
			"project": "v3",
			"task_id": "task-xyz",
		},
	}
	_ = h.Trigger(context.Background(), event)
}

func TestContextThresholdHook_AboveThreshold_DefaultMetadata(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_rj_default_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)
	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	cm := NewContextManager(db, t.TempDir())
	defer cm.Stop()
	h := &ContextThresholdHook{cm: cm}
	event := HookEvent{
		Type:       "context_threshold",
		AgentID:    "agent-3",
		ContextPct: 75.0,
		Metadata:   map[string]interface{}{}, // triggers default domain/project/task
	}
	_ = h.Trigger(context.Background(), event)
}

// errorHook always returns an error.
type errorHook struct{}

func (e *errorHook) Name() string { return "error-hook" }
func (e *errorHook) Trigger(_ context.Context, _ HookEvent) error {
	return errors.New("hook failed")
}

// callbackHook runs a callback function.
type callbackHook struct{ fn func() }

func (c *callbackHook) Name() string { return "callback-hook" }
func (c *callbackHook) Trigger(_ context.Context, _ HookEvent) error {
	c.fn()
	return nil
}
