//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// Wave 51: handleTaskList (main.go delegate), autoPromoteCompletedTasksInLane,
//          writeTokenBudgetFile, WorktreeManager.CreateWorktree

// ─── handleTaskList (main.go) ─────────────────────────────────────────────

func TestHandleTaskList_JSON_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskList_FormatText_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/list?format=text", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── autoPromoteCompletedTasksInLane ─────────────────────────────────────

func TestAutoPromoteCompletedTasksInLane_NilLaneManager_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a COMPLETED task in 'dev' lane (updated > 60 seconds ago via direct SQL).
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, lane, created_at, updated_at)
		VALUES ('TASK-W51-PROMO', 'test', 'proj', 'feature', 'Promo Task', 'completed', 'COMPLETED', 50, 'dev',
		        datetime('now', '-120 seconds'), datetime('now', '-120 seconds'))
	`)

	// With globalLaneManager=nil, the inner loop breaks immediately.
	// Just ensure no panic/error.
	if err := autoPromoteCompletedTasksInLane(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestAutoPromoteCompletedTasksInLane_NoTasks_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// No COMPLETED tasks in non-done lanes → returns nil immediately.
	if err := autoPromoteCompletedTasksInLane(context.Background(), db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// ─── writeTokenBudgetFile ─────────────────────────────────────────────────

func TestWriteTokenBudgetFile_Success_W51(t *testing.T) {
	tmpDir := t.TempDir()
	path := filepath.Join(tmpDir, "token_budget.json")

	f := &tokenBudgetFile{
		Node:      "prya",
		Providers: map[string]tbProviderData{},
	}

	if err := writeTokenBudgetFile(path, f); err != nil {
		t.Errorf("expected nil, got %v", err)
	}

	// Verify file was written.
	if _, err := os.Stat(path); err != nil {
		t.Errorf("expected file to exist: %v", err)
	}
}

func TestWriteTokenBudgetFile_BadPath_W51(t *testing.T) {
	// Write to non-existent directory → should fail.
	path := "/nonexistent-dir-w51/token_budget.json"
	f := &tokenBudgetFile{Node: "test"}
	err := writeTokenBudgetFile(path, f)
	if err == nil {
		t.Error("expected error for bad path, got nil")
	}
}

// ─── WorktreeManager.CreateWorktree ──────────────────────────────────────

func TestWorktreeManager_CreateWorktree_EmptyTaskID_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	m := NewWorktreeManager(tmpDir, filepath.Join(tmpDir, "worktrees"), db)

	_, err := m.CreateWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID, got nil")
	}
}

func TestWorktreeManager_CreateWorktree_ExistingDir_W51(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	worktreeDir := filepath.Join(tmpDir, "worktrees")
	m := NewWorktreeManager(tmpDir, worktreeDir, db)

	// Pre-create the worktree directory for TASK-W51-WT.
	taskPath := filepath.Join(worktreeDir, "TASK-W51-WT")
	_ = os.MkdirAll(taskPath, 0755)

	// Should return the existing path without error.
	path, err := m.CreateWorktree(context.Background(), "TASK-W51-WT")
	if err != nil {
		t.Errorf("expected nil for existing dir, got %v", err)
	}
	if path == "" {
		t.Error("expected non-empty path")
	}
}
