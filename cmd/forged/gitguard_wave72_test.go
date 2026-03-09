//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"os/exec"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// gitguard.go: executeCommit — branch mismatch path
// These tests target the ~62% of statements still uncovered in executeCommit
// ---------------------------------------------------------------------------

func TestWave72_GitGuard_ExecuteCommit_BranchMismatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use FORGE repo root so getCurrentBranch succeeds
	repoDir := "/Users/bogdan/work/FORGE"
	gg := NewGitGuard(db, repoDir)

	// Lock to a branch that doesn't exist / is not current
	_, err := db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-mismatch", "feature/nonexistent-branch-99999", time.Now(), "test")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	result := gg.executeCommit(GitAction{
		TaskID:  "task-mismatch",
		Message: "test commit",
		Files:   []string{"README.md"},
	})

	if result.Success {
		t.Error("expected failure due to branch mismatch")
	}
	if result.Error == "" {
		t.Error("expected non-empty error message")
	}
}

func TestWave72_GitGuard_ExecuteCommit_WithFiles_StagingFails(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use a temp dir that is NOT a git repo — staging will fail
	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	// getCurrentBranch will fail since no git repo — that's the first error
	result := gg.executeCommit(GitAction{
		TaskID:  "task-stage-fail",
		Message: "test commit",
		Files:   []string{"nonexistent.go"},
	})

	if result.Success {
		t.Error("expected failure in non-git dir")
	}
	if result.Error == "" {
		t.Error("expected error message")
	}
}

// ---------------------------------------------------------------------------
// gitguard.go: executePush — no branch lock path
// ---------------------------------------------------------------------------

func TestWave72_GitGuard_ExecutePush_NoBranchLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	result := gg.executePush(GitAction{
		TaskID: "task-no-lock-push",
	})

	if result.Success {
		t.Error("expected failure with no branch lock")
	}
	if result.Error == "" {
		t.Error("expected error message")
	}
}

func TestWave72_GitGuard_ExecutePush_NoBranchLock_WithSpecificBranch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	result := gg.executePush(GitAction{
		TaskID: "task-no-lock-push2",
		Branch: "main",
	})

	if result.Success {
		t.Error("expected failure with no branch lock")
	}
}

func TestWave72_GitGuard_ExecutePush_WithLock_GitFails(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use a temp dir — not a git repo, push will fail
	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	_, err := db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-push-fail", "main", time.Now(), "test")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	result := gg.executePush(GitAction{
		TaskID: "task-push-fail",
	})

	// Should fail because it's not a real git repo with remote
	if result.Success {
		t.Error("expected git push to fail in non-repo temp dir")
	}
}

func TestWave72_GitGuard_ExecutePush_WithLock_WithBranch_GitFails(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	repoDir := t.TempDir()
	gg := NewGitGuard(db, repoDir)

	_, err := db.Exec(`INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by) VALUES (?, ?, ?, ?)`,
		"task-push-branch", "feature/x", time.Now(), "test")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	result := gg.executePush(GitAction{
		TaskID: "task-push-branch",
		Branch: "main",
	})

	if result.Success {
		t.Error("expected git push to fail")
	}
}

// ---------------------------------------------------------------------------
// gitguard.go: getModifiedFiles — covered via executeCommit paths
// ---------------------------------------------------------------------------

func TestWave72_GitGuard_GetModifiedFiles_ValidRepo(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use FORGE root which is a real git repo on this machine
	// Try the known Linux path first, then fall back to a temp git repo
	repoDir := "/home/openclaw/work/FORGE"
	if _, err := os.Stat(repoDir); os.IsNotExist(err) {
		// Fallback: create a minimal git repo in a temp dir
		repoDir = t.TempDir()
		if err := exec.Command("git", "init", repoDir).Run(); err != nil {
			t.Skipf("git init failed, skipping: %v", err)
		}
	}

	gg := NewGitGuard(db, repoDir)
	files, err := gg.getModifiedFiles()
	// Should succeed — zero or more files
	if err != nil {
		t.Errorf("getModifiedFiles should not fail on valid repo: %v", err)
	}
	// files may be empty (clean repo) — that's fine
	_ = files
}

func TestWave72_GitGuard_GetModifiedFiles_InvalidRepo(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	_, err := gg.getModifiedFiles()
	if err == nil {
		t.Error("expected error in non-git directory")
	}
}

// ---------------------------------------------------------------------------
// gitguard.go: Execute — action routing (push/merge/status paths)
// ---------------------------------------------------------------------------

func TestWave72_GitGuard_Execute_Push(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	result, err := gg.Execute(context.Background(), GitGuardRequest{
		TaskID: "task-exec-push",
		Action: "push",
	})

	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	// Should fail — no branch lock
	if result.Success {
		t.Error("expected failure: no branch lock for push")
	}
}

func TestWave72_GitGuard_Execute_Status(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, "/Users/bogdan/work/FORGE")
	result, err := gg.Execute(context.Background(), GitGuardRequest{
		TaskID: "task-exec-status",
		Action: "status",
	})

	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
}

func TestWave72_GitGuard_Execute_Commit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	result, err := gg.Execute(context.Background(), GitGuardRequest{
		TaskID:  "task-exec-commit",
		Action:  "commit",
		Message: "test",
	})

	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	// Should fail — no branch lock, not a git repo
	if result.Success {
		t.Error("expected failure")
	}
}
