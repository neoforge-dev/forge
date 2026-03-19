//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 155: gitguard.go functions
// Targets:
//   computePayloadHash         (gitguard.go:86) — deterministic hash
//   GitGuard.CheckSafe         (gitguard.go:185) — safe repo, index.lock, MERGE_HEAD
//   GitGuard.getExistingAction (gitguard.go:101) — no rows (nil), found row
//   GitGuard.GetBranchLock     (gitguard.go:567) — no lock → empty
//   GitGuard.ReleaseBranchLock (gitguard.go:561) — no lock → no error
// ---------------------------------------------------------------------------

func newTestGitGuard(t *testing.T) *GitGuard {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	return NewGitGuard(db, t.TempDir())
}

// ---------------------------------------------------------------------------
// computePayloadHash
// ---------------------------------------------------------------------------

func TestWave155_ComputePayloadHash_Deterministic(t *testing.T) {
	action := GitAction{
		TaskID:  "task-001",
		Type:    "commit",
		Branch:  "feat/foo",
		Message: "test commit",
		Files:   []string{"a.go", "b.go"},
		Author:  "kimi",
	}
	h1 := computePayloadHash(action)
	h2 := computePayloadHash(action)
	if h1 != h2 {
		t.Errorf("hash not deterministic: %s vs %s", h1, h2)
	}
	if len(h1) != 64 {
		t.Errorf("expected 64-char SHA256 hex, got %d chars: %s", len(h1), h1)
	}
}

func TestWave155_ComputePayloadHash_Different(t *testing.T) {
	a1 := GitAction{TaskID: "t1", Type: "commit", Branch: "main", Message: "m1"}
	a2 := GitAction{TaskID: "t2", Type: "commit", Branch: "main", Message: "m1"}
	if computePayloadHash(a1) == computePayloadHash(a2) {
		t.Error("different actions should produce different hashes")
	}
}

// ---------------------------------------------------------------------------
// GitGuard.CheckSafe
// ---------------------------------------------------------------------------

func TestWave155_CheckSafe_SafeRepo(t *testing.T) {
	repoRoot := t.TempDir()
	// Create minimal .git dir (no lock files)
	if err := os.MkdirAll(filepath.Join(repoRoot, ".git"), 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	g := NewGitGuard(db, repoRoot)
	ok, reason := g.CheckSafe()
	if !ok {
		t.Errorf("expected safe, got reason: %s", reason)
	}
}

func TestWave155_CheckSafe_IndexLock(t *testing.T) {
	repoRoot := t.TempDir()
	gitDir := filepath.Join(repoRoot, ".git")
	if err := os.MkdirAll(gitDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Create index.lock
	if err := os.WriteFile(filepath.Join(gitDir, "index.lock"), []byte(""), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	g := NewGitGuard(db, repoRoot)
	ok, reason := g.CheckSafe()
	if ok {
		t.Error("expected unsafe due to index.lock")
	}
	if reason == "" {
		t.Error("expected non-empty reason")
	}
}

func TestWave155_CheckSafe_MergeHead(t *testing.T) {
	repoRoot := t.TempDir()
	gitDir := filepath.Join(repoRoot, ".git")
	if err := os.MkdirAll(gitDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Create MERGE_HEAD
	if err := os.WriteFile(filepath.Join(gitDir, "MERGE_HEAD"), []byte("abc123"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	g := NewGitGuard(db, repoRoot)
	ok, reason := g.CheckSafe()
	if ok {
		t.Error("expected unsafe due to MERGE_HEAD")
	}
	if reason == "" {
		t.Error("expected non-empty reason")
	}
}

// ---------------------------------------------------------------------------
// GitGuard.getExistingAction — nil when no matching row
// ---------------------------------------------------------------------------

func TestWave155_GetExistingAction_NoRows(t *testing.T) {
	g := newTestGitGuard(t)
	action, err := g.getExistingAction(context.Background(), "nonexistent-hash")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if action != nil {
		t.Errorf("expected nil action for nonexistent hash, got %+v", action)
	}
}

// ---------------------------------------------------------------------------
// GitGuard.GetBranchLock + ReleaseBranchLock
// ---------------------------------------------------------------------------

func TestWave155_GetBranchLock_NoLock(t *testing.T) {
	g := newTestGitGuard(t)
	branch, err := g.GetBranchLock(context.Background(), "task-nonexistent")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if branch != "" {
		t.Errorf("expected empty branch for unlocked task, got %q", branch)
	}
}

func TestWave155_ReleaseBranchLock_NoLock(t *testing.T) {
	g := newTestGitGuard(t)
	// Releasing a lock that doesn't exist should not error
	err := g.ReleaseBranchLock(context.Background(), "task-nonexistent")
	if err != nil {
		t.Errorf("expected nil for releasing nonexistent lock, got: %v", err)
	}
}
