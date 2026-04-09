package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRecoverCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("recover")
	if err != nil {
		t.Errorf("recover command failed: %v", err)
	}
}

func TestRecoverDryRun(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("recover", "--dry-run")
	if err != nil {
		t.Errorf("recover --dry-run failed: %v", err)
	}
}

func TestRecoverRemovesStaleLock(t *testing.T) {
	// Create a temporary directory to simulate a git repo
	tmpDir := t.TempDir()
	gitDir := filepath.Join(tmpDir, ".git")
	os.Mkdir(gitDir, 0755)

	// Create a stale 0-byte index.lock
	lockFile := filepath.Join(gitDir, "index.lock")
	os.WriteFile(lockFile, []byte{}, 0644)

	// Change to temp dir, run recover directly, change back
	origDir, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(origDir)

	// Use runRecover directly instead of test runner (avoids command registration issues)
	cmd := recoverCmd
	cmd.ResetFlags()
	cmd.Flags().Bool("dry-run", false, "")
	err := runRecover(cmd, nil)
	if err != nil {
		t.Errorf("recover failed: %v", err)
	}

	// Verify lock was removed
	if _, err := os.Stat(lockFile); err == nil {
		t.Error("expected stale index.lock to be removed")
	}
}

func TestCheckGitBlockersFunction(t *testing.T) {
	// checkGitBlockers should return a string (may or may not be empty depending on repo state)
	result := checkGitBlockers()
	// Just verify it doesn't panic — actual blockers depend on repo state
	t.Logf("checkGitBlockers result: %q", result)
}
