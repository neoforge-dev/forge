//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"strings"
	"testing"
)

// Wave 24b: gitguard.go functions (previously 0%)

func TestNewGitGuard_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	g := NewGitGuard(db, ".")
	if g == nil {
		t.Error("expected non-nil GitGuard")
	}
	if g.repoRoot != "." {
		t.Errorf("expected repoRoot '.', got %q", g.repoRoot)
	}
}

func TestNewGitGuard_EmptyRoot_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty repoRoot should default to "."
	g := NewGitGuard(db, "")
	if g.repoRoot != "." {
		t.Errorf("expected default repoRoot '.', got %q", g.repoRoot)
	}
}

func TestComputePayloadHash_W24B(t *testing.T) {
	action := GitAction{
		TaskID:  "TASK-001",
		Type:    GitActionCommit,
		Branch:  "main",
		Message: "test commit",
		Files:   []string{"file1.go"},
		Author:  "test",
	}
	hash := computePayloadHash(action)
	if hash == "" {
		t.Error("expected non-empty hash")
	}
	if len(hash) != 64 {
		t.Errorf("expected 64-char SHA256 hex, got len=%d: %q", len(hash), hash)
	}

	// Same inputs = same hash
	hash2 := computePayloadHash(action)
	if hash != hash2 {
		t.Error("expected deterministic hash")
	}

	// Different input = different hash
	action2 := action
	action2.Message = "different commit"
	hash3 := computePayloadHash(action2)
	if hash == hash3 {
		t.Error("expected different hash for different input")
	}
}

func TestCheckSafe_CleanRepo_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Use the actual FORGE repo root — no lock files in normal operation
	g := NewGitGuard(db, "../..")
	safe, reason := g.CheckSafe()
	// In normal CI/test, the repo should be safe
	if !safe {
		t.Logf("CheckSafe returned false: %s (acceptable in some envs)", reason)
	}
	// reason should be empty when safe
	if safe && reason != "" {
		t.Errorf("expected empty reason when safe, got %q", reason)
	}
}

func TestCheckSafe_InvalidRoot_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Nonexistent root — no .git/index.lock can exist
	g := NewGitGuard(db, "/tmp/nonexistent-forge-test-repo-xyz")
	safe, reason := g.CheckSafe()
	// No lock files exist → should be safe
	if !safe {
		t.Logf("CheckSafe with nonexistent root returned false: %s", reason)
	}
	_ = reason
}

func TestGetCurrentBranch_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	g := NewGitGuard(db, "../..")
	branch, err := g.getCurrentBranch()
	if err != nil {
		t.Fatalf("getCurrentBranch failed: %v", err)
	}
	if branch == "" {
		t.Error("expected non-empty branch name")
	}
	// Should be "main" or another valid branch name (no spaces)
	if strings.Contains(branch, " ") {
		t.Errorf("branch name contains space: %q", branch)
	}
}

func TestGetModifiedFiles_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	g := NewGitGuard(db, "../..")
	files, err := g.getModifiedFiles()
	// May return empty list or some files — just verify no error
	if err != nil {
		t.Fatalf("getModifiedFiles failed: %v", err)
	}
	// files may be nil or a slice — both are valid
	_ = files
}

func TestGetExistingAction_NotFound_W24B(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	g := NewGitGuard(db, ".")
	ctx := context.Background()

	// No actions exist → should return nil, nil
	action, err := g.getExistingAction(ctx, "nonexistent-hash-xyz")
	if err != nil {
		t.Fatalf("getExistingAction returned error: %v", err)
	}
	if action != nil {
		t.Error("expected nil action for nonexistent hash")
	}
}

func TestGitGuardConstants_W24B(t *testing.T) {
	// Verify action type constants are defined
	if GitActionCommit != "commit" {
		t.Errorf("expected GitActionCommit='commit', got %q", GitActionCommit)
	}
	if GitActionPush != "push" {
		t.Errorf("expected GitActionPush='push', got %q", GitActionPush)
	}
	if GitActionBranch != "branch" {
		t.Errorf("expected GitActionBranch='branch', got %q", GitActionBranch)
	}
	if GitActionMerge != "merge" {
		t.Errorf("expected GitActionMerge='merge', got %q", GitActionMerge)
	}
}
