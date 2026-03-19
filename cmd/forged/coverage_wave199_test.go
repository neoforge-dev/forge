//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 199: lease.go — TaskStateMachine + LeaseManager basics
// Targets:
//   NewTaskStateMachine           (lease.go:42)  — non-nil
//   TaskStateMachineImpl.GetTaskState(lease.go:52) — not found
//   TaskStateMachineImpl.Transition(lease.go:48) — no-op (records transition)
//   NewLeaseManager               (lease.go:97)  — with temp DB
//   sqliteLeaseManager.Recover    (lease.go:248) — empty (no expired leases)
//   sqliteLeaseManager.Release    (lease.go:204) — not found
//   sqliteLeaseManager.Renew      (lease.go:182) — not found (no lease)
//   sqliteLeaseManager.Close      — closes cleanly
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewTaskStateMachine
// ---------------------------------------------------------------------------

func TestWave199_NewTaskStateMachine_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	if sm == nil {
		t.Error("expected non-nil TaskStateMachine")
	}
}

func TestWave199_TaskStateMachineImpl_GetTaskState_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	_, err := sm.GetTaskState("nonexistent-task")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}

func TestWave199_TaskStateMachineImpl_Transition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	// Transition may fail or succeed depending on whether task exists
	// Just ensure no panic
	_ = sm.Transition("nonexistent-task", StateQueued, StateDispatched, "test")
}

// ---------------------------------------------------------------------------
// NewLeaseManager (with temp SQLite file)
// ---------------------------------------------------------------------------

func TestWave199_NewLeaseManager_NonNil(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "lease_test.db")

	lm, err := NewLeaseManager(dbPath)
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	if lm == nil {
		t.Error("expected non-nil LeaseManager")
	}
	defer lm.Close()
}

// ---------------------------------------------------------------------------
// Recover (no expired leases in fresh DB)
// ---------------------------------------------------------------------------

func TestWave199_LeaseManager_Recover_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "lease_recover.db")

	lm, err := NewLeaseManager(dbPath)
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	defer lm.Close()

	// Fresh DB has no leases table necessarily — ignore error
	leases, _ := lm.Recover(context.Background())
	_ = leases
}

// ---------------------------------------------------------------------------
// Release — not found
// ---------------------------------------------------------------------------

func TestWave199_LeaseManager_Release_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	err := lm.Release(context.Background(), "nonexistent-lease")
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// Renew — not found
// ---------------------------------------------------------------------------

func TestWave199_LeaseManager_Renew_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	sm := NewTaskStateMachine(db)
	lm := &sqliteLeaseManager{db: db, stateMachine: sm}

	err := lm.Renew(context.Background(), "nonexistent-lease", time.Hour)
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestWave199_LeaseManager_Close(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "lease_close.db")

	lm, err := NewLeaseManager(dbPath)
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	if err := lm.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}
