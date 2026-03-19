//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 211: worktree_manager.go
// Targets:
//   NewWorktreeManager           (worktree_manager.go:31) — empty args / normal
//   WorktreeManager.ListWorktrees(worktree_manager.go:133) — uses real git repo
//   WorktreeManager.CreateWorktree (worktree_manager.go:49) — empty taskID
//   WorktreeManager.RecordWorktree (worktree_manager.go:190)
//   WorktreeManager.PruneStaleWorktrees (worktree_manager.go:202) — empty dir
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewWorktreeManager
// ---------------------------------------------------------------------------

func TestWave211_NewWorktreeManager_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager("/tmp", "/tmp/worktrees", db)
	if wm == nil {
		t.Error("expected non-nil WorktreeManager")
	}
}

func TestWave211_NewWorktreeManager_EmptyArgs(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty repoRoot → uses current dir
	wm := NewWorktreeManager("", "", db)
	if wm == nil {
		t.Error("expected non-nil WorktreeManager with empty args")
	}
}

// ---------------------------------------------------------------------------
// CreateWorktree — empty taskID
// ---------------------------------------------------------------------------

func TestWave211_CreateWorktree_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager(t.TempDir(), t.TempDir(), db)
	_, err := wm.CreateWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID")
	}
}

// ---------------------------------------------------------------------------
// ListWorktrees — uses real git repo (FORGE root)
// ---------------------------------------------------------------------------

func TestWave211_ListWorktrees_ReturnsAtLeastOne(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use FORGE repo root which is a real git repo
	repoRoot := os.Getenv("FORGE_ROOT")
	if repoRoot == "" {
		repoRoot = "../.."
	}
	wm := NewWorktreeManager(repoRoot, repoRoot+"/.forge/worktrees", db)
	wts, err := wm.ListWorktrees(context.Background())
	if err != nil {
		t.Fatalf("ListWorktrees: %v", err)
	}
	// At minimum the main worktree should be listed
	if len(wts) == 0 {
		t.Error("expected at least 1 worktree (main)")
	}
}

// ---------------------------------------------------------------------------
// RecordWorktree
// ---------------------------------------------------------------------------

func TestWave211_RecordWorktree_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager("/tmp", "/tmp/worktrees", db)
	err := wm.RecordWorktree(context.Background(), "wave211-task", "/tmp/worktrees/wave211-task", "feature/wave211-task", "agent-1")
	if err != nil {
		t.Errorf("RecordWorktree: %v", err)
	}
}

// ---------------------------------------------------------------------------
// PruneStaleWorktrees — empty/nonexistent dir
// ---------------------------------------------------------------------------

func TestWave211_PruneStaleWorktrees_EmptyDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager("/tmp", t.TempDir(), db)
	err := wm.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Errorf("PruneStaleWorktrees on empty dir: %v", err)
	}
}

func TestWave211_PruneStaleWorktrees_NonExistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	wm := NewWorktreeManager("/tmp", "/nonexistent/worktrees/wave211", db)
	// Should return nil (directory doesn't exist)
	err := wm.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Errorf("PruneStaleWorktrees on nonexistent dir: %v", err)
	}
}
