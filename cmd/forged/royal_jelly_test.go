//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
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

	cm := testContextManager(t, db, t.TempDir())
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

	cm := testContextManager(t, db, t.TempDir())
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

// Wave 145: syncRoyalJelly uncovered branches
// Target: patrol.go syncRoyalJelly

// TestWave145_SyncRoyalJelly_NoDirReturnsNil verifies that syncRoyalJelly
// returns nil when the .forge/context directory does not exist.
func TestWave145_SyncRoyalJelly_NoDirReturnsNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Point FORGE_ROOT to a temp dir that has NO .forge/context directory.
	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	ctx := context.Background()
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("expected nil when context dir missing, got: %v", err)
	}
}

// TestWave145_SyncRoyalJelly_WithNonDirEntry covers the !e.IsDir() skip branch.
// A regular file is placed directly in .forge/context — it should be skipped.
func TestWave145_SyncRoyalJelly_WithNonDirEntry(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	contextDir := filepath.Join(root, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir context: %v", err)
	}
	// Place a regular file (not a directory) in the context dir.
	if err := os.WriteFile(filepath.Join(contextDir, "not-a-dir.md"), []byte("content"), 0644); err != nil {
		t.Fatalf("write file: %v", err)
	}

	t.Setenv("FORGE_ROOT", root)
	ctx := context.Background()
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("expected nil when context dir has only non-dir entries, got: %v", err)
	}
}

// TestWave145_SyncRoyalJelly_WithContextPctFile covers the loop body:
// a domain directory with a context_pct file triggers the agent update.
func TestWave145_SyncRoyalJelly_WithContextPctFile(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	domainDir := filepath.Join(root, ".forge", "context", "forge")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("mkdir domain: %v", err)
	}
	// Write context_pct file with a valid float.
	if err := os.WriteFile(filepath.Join(domainDir, "context_pct"), []byte("42.5\n"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	t.Setenv("FORGE_ROOT", root)
	ctx := context.Background()
	// The UPDATE may affect 0 rows (no agent named "forge" in test DB), but
	// it should not error — the function just skips if no rows match.
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("expected nil with valid domain+pct file, got: %v", err)
	}
}

// TestWave145_SyncRoyalJelly_InvalidPctFile covers the Sscanf error skip:
// a context_pct file with non-numeric content → loop continues.
func TestWave145_SyncRoyalJelly_InvalidPctFile(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	domainDir := filepath.Join(root, ".forge", "context", "myagent")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("mkdir domain: %v", err)
	}
	// Non-numeric content → Sscanf fails → continue.
	if err := os.WriteFile(filepath.Join(domainDir, "context_pct"), []byte("not-a-number"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	t.Setenv("FORGE_ROOT", root)
	ctx := context.Background()
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("expected nil with invalid pct file, got: %v", err)
	}
}
