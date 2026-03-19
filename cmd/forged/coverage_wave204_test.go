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

// ---------------------------------------------------------------------------
// Wave 204: gitguard.go
// Targets:
//   NewGitGuard              (gitguard.go:75)  — non-nil / empty repoRoot
//   computePayloadHash       (gitguard.go:86)  — deterministic / different
//   GitGuard.CheckSafe       (gitguard.go:185) — safe dir / index.lock
//   GitGuard.GetBranchLock   (gitguard.go:567) — not found
//   GitGuard.ReleaseBranchLock(gitguard.go:561)— not found OK
//   gitguardHandler          (gitguard.go:577) — GET list / GET with task filter
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewGitGuard
// ---------------------------------------------------------------------------

func TestWave204_NewGitGuard_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	if gg == nil {
		t.Error("expected non-nil GitGuard")
	}
}

func TestWave204_NewGitGuard_EmptyRepoRoot(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty repoRoot defaults to "."
	gg := NewGitGuard(db, "")
	if gg == nil {
		t.Error("expected non-nil GitGuard with empty repoRoot")
	}
}

// ---------------------------------------------------------------------------
// computePayloadHash
// ---------------------------------------------------------------------------

func TestWave204_ComputePayloadHash_Deterministic(t *testing.T) {
	action := GitAction{
		TaskID:  "task-1",
		Type:    "commit",
		Branch:  "main",
		Message: "test commit",
		Files:   []string{"file.go"},
		Author:  "agent-1",
	}
	h1 := computePayloadHash(action)
	h2 := computePayloadHash(action)
	if h1 != h2 {
		t.Errorf("expected deterministic hash, got %q and %q", h1, h2)
	}
	if h1 == "" {
		t.Error("expected non-empty hash")
	}
}

func TestWave204_ComputePayloadHash_DifferentActions(t *testing.T) {
	a1 := GitAction{TaskID: "task-1", Type: "commit"}
	a2 := GitAction{TaskID: "task-2", Type: "commit"}
	if computePayloadHash(a1) == computePayloadHash(a2) {
		t.Error("expected different hashes for different actions")
	}
}

// ---------------------------------------------------------------------------
// CheckSafe
// ---------------------------------------------------------------------------

func TestWave204_CheckSafe_SafeDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use a temp dir with no .git lock files → should be safe
	gg := NewGitGuard(db, t.TempDir())
	safe, reason := gg.CheckSafe()
	if !safe {
		t.Errorf("expected safe=true for clean dir, got reason=%q", reason)
	}
}

func TestWave204_CheckSafe_IndexLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create .git/index.lock to simulate a locked git repo
	tmpDir := t.TempDir()
	gitDir := filepath.Join(tmpDir, ".git")
	if err := os.MkdirAll(gitDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	lockFile := filepath.Join(gitDir, "index.lock")
	if err := os.WriteFile(lockFile, []byte("locked"), 0644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	gg := NewGitGuard(db, tmpDir)
	safe, reason := gg.CheckSafe()
	if safe {
		t.Error("expected safe=false with index.lock present")
	}
	if reason == "" {
		t.Error("expected non-empty reason")
	}
}

// ---------------------------------------------------------------------------
// GetBranchLock / ReleaseBranchLock
// ---------------------------------------------------------------------------

func TestWave204_GetBranchLock_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	branch, err := gg.GetBranchLock(context.Background(), "nonexistent-task")
	if err != nil {
		t.Errorf("GetBranchLock: %v", err)
	}
	if branch != "" {
		t.Errorf("expected empty branch for nonexistent task, got %q", branch)
	}
}

func TestWave204_ReleaseBranchLock_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	// Releasing a non-existent lock should succeed (no-op DELETE)
	err := gg.ReleaseBranchLock(context.Background(), "nonexistent-task")
	if err != nil {
		t.Errorf("ReleaseBranchLock: %v", err)
	}
}

// ---------------------------------------------------------------------------
// gitguardHandler
// ---------------------------------------------------------------------------

func TestWave204_GitguardHandler_GetList(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	handler := gitguardHandler(gg)

	req := httptest.NewRequest(http.MethodGet, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave204_GitguardHandler_GetWithTaskFilter(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	handler := gitguardHandler(gg)

	req := httptest.NewRequest(http.MethodGet, "/api/gitguard?task_id=task-99", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with task filter, got %d: %s", w.Code, w.Body.String())
	}
}
