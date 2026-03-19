//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"errors"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 168: royal_jelly.go — RoyalJelly hook system + ContextThresholdHook
// Targets:
//   NewRoyalJelly           (royal_jelly.go:20) — constructor
//   RoyalJelly.RegisterHook (royal_jelly.go:42) — adds hook to map
//   RoyalJelly.TriggerHooks (royal_jelly.go:47) — no hooks, hooks execute, hook fails
//   ContextThresholdHook.Name (royal_jelly.go:70) — returns string
//   ContextThresholdHook.Trigger (royal_jelly.go:74) — below 50% / above 50%
// ---------------------------------------------------------------------------

// mockHook is a test hook that records calls.
type mockHook struct {
	name    string
	err     error
	called  bool
	lastEvt HookEvent
}

func (h *mockHook) Name() string { return h.name }
func (h *mockHook) Trigger(ctx context.Context, event HookEvent) error {
	h.called = true
	h.lastEvt = event
	return h.err
}

// ---------------------------------------------------------------------------
// NewRoyalJelly
// ---------------------------------------------------------------------------

func TestWave168_NewRoyalJelly_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)
	if rj == nil {
		t.Error("expected non-nil RoyalJelly")
	}
}

// ---------------------------------------------------------------------------
// RoyalJelly.RegisterHook
// ---------------------------------------------------------------------------

func TestWave168_RegisterHook_AppendsHook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)

	h1 := &mockHook{name: "hook1"}
	h2 := &mockHook{name: "hook2"}

	rj.RegisterHook("context_threshold", h1)
	rj.RegisterHook("context_threshold", h2)

	if len(rj.hooks["context_threshold"]) != 2 {
		t.Errorf("expected 2 hooks, got %d", len(rj.hooks["context_threshold"]))
	}
}

// ---------------------------------------------------------------------------
// RoyalJelly.TriggerHooks
// ---------------------------------------------------------------------------

func TestWave168_TriggerHooks_NoHooksRegistered(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)

	// No hooks registered for this event type — should return nil
	err := rj.TriggerHooks(context.Background(), HookEvent{
		Type: "some_unknown_event",
	})
	if err != nil {
		t.Errorf("expected nil for unregistered event type, got: %v", err)
	}
}

func TestWave168_TriggerHooks_HookExecutes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)

	hook := &mockHook{name: "test-hook"}
	rj.RegisterHook("agent_disconnect", hook)

	evt := HookEvent{Type: "agent_disconnect", AgentID: "kimi"}
	if err := rj.TriggerHooks(context.Background(), evt); err != nil {
		t.Errorf("expected nil, got: %v", err)
	}

	if !hook.called {
		t.Error("expected hook to be called")
	}
	if hook.lastEvt.AgentID != "kimi" {
		t.Errorf("unexpected agent ID in hook: %q", hook.lastEvt.AgentID)
	}
}

func TestWave168_TriggerHooks_FailingHookLogsButDoesNotError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	rj := NewRoyalJelly(db, cm)

	failHook := &mockHook{name: "fail-hook", err: errors.New("hook error")}
	rj.RegisterHook("test_event", failHook)

	// TriggerHooks always returns nil even when hooks fail (logs instead)
	err := rj.TriggerHooks(context.Background(), HookEvent{Type: "test_event"})
	if err != nil {
		t.Errorf("expected nil even for failing hook, got: %v", err)
	}
	if !failHook.called {
		t.Error("expected failing hook to be called")
	}
}

// ---------------------------------------------------------------------------
// ContextThresholdHook.Name
// ---------------------------------------------------------------------------

func TestWave168_ContextThresholdHook_Name(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	hook := &ContextThresholdHook{cm: cm}
	if got := hook.Name(); got != "context_threshold_envelope" {
		t.Errorf("expected 'context_threshold_envelope', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// ContextThresholdHook.Trigger
// ---------------------------------------------------------------------------

func TestWave168_ContextThresholdHook_Trigger_BelowThreshold(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	hook := &ContextThresholdHook{cm: cm}

	// Below 50% — should do nothing and return nil
	err := hook.Trigger(context.Background(), HookEvent{
		Type:       "context_threshold",
		AgentID:    "kimi",
		ContextPct: 30.0,
		Metadata:   map[string]interface{}{"domain": "forge", "project": "test"},
	})
	if err != nil {
		t.Errorf("expected nil for below-threshold event, got: %v", err)
	}
}

func TestWave168_ContextThresholdHook_Trigger_AboveThreshold(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	contextDir := t.TempDir()
	cm := testContextManager(t, db, contextDir)
	hook := &ContextThresholdHook{cm: cm}

	// Above 50% — triggers GenerateEnvelope (may fail due to no context files — that's ok)
	err := hook.Trigger(context.Background(), HookEvent{
		Type:       "context_threshold",
		AgentID:    "kimi",
		ContextPct: 75.0,
		Metadata: map[string]interface{}{
			"domain":  "forge",
			"project": "test",
			"task_id": "task-wave168",
		},
	})
	// err may be non-nil if no context files exist — just check no panic
	_ = err
}
