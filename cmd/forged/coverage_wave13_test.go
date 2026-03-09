//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave13_test.go — Coverage wave 13: completion, migrate, registry, metrics hijack
// Target: +1pp from ~58.9% to ~60%
package main

import (
	"bytes"
	"net/http/httptest"
	"testing"
	"time"
)

// --- completion.go: HandleCompletion ---

func TestHandleCompletion_Bash(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty bash completion script")
	}
}

func TestHandleCompletion_Zsh(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty zsh completion script")
	}
}

func TestHandleCompletion_Fish(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty fish completion script")
	}
}

// --- migrate.go: MigrateDown, loadMigrations ---

func TestMigrateDown_ZeroSteps(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// steps=0 → returns nil immediately
	if err := MigrateDown(getDBConn(), 0); err != nil {
		t.Errorf("MigrateDown(0): %v", err)
	}
}

func TestMigrateDown_NegativeSteps(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	if err := MigrateDown(getDBConn(), -1); err != nil {
		t.Errorf("MigrateDown(-1): %v", err)
	}
}

func TestLoadMigrations(t *testing.T) {
	migrations, err := loadMigrations()
	if err != nil {
		t.Fatalf("loadMigrations: %v", err)
	}
	if len(migrations) == 0 {
		t.Error("expected at least one migration")
	}
	// Versions should be positive
	for _, m := range migrations {
		if m.Version <= 0 {
			t.Errorf("migration version should be > 0, got %d", m.Version)
		}
	}
}

// --- registry.go: StartCleanup, StopCleanup ---

func TestAgentRegistry_StartStop(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	r := NewAgentRegistry(db, 5*time.Minute)

	// StartCleanup should not panic
	r.StartCleanup(100 * time.Millisecond)

	// Calling Start twice should be idempotent (cleanupOnce)
	r.StartCleanup(100 * time.Millisecond)

	// Let it tick at least once
	time.Sleep(150 * time.Millisecond)

	// StopCleanup should not panic
	r.StopCleanup()
}

// --- metrics.go: Hijack ---

func TestResponseWriter_Hijack_NotSupported(t *testing.T) {
	inner := httptest.NewRecorder()
	rw := &responseWriter{
		ResponseWriter: inner,
	}
	_, _, err := rw.Hijack()
	if err == nil {
		t.Error("expected error when underlying writer doesn't support Hijack")
	}
}

// --- handlers_task.go: completeTaskWithApprovalHandler ---
