package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// shouldUseSafeMode
// ---------------------------------------------------------------------------

func TestShouldUseSafeMode_ForceEnabled(t *testing.T) {
	t.Setenv("GITSAFE_ENABLED", "1")
	if !shouldUseSafeMode() {
		t.Error("GITSAFE_ENABLED=1 should force safe mode on any node")
	}
}

func TestShouldUseSafeMode_ForceDisabled(t *testing.T) {
	t.Setenv("GITSAFE_ENABLED", "0")
	if shouldUseSafeMode() {
		t.Error("GITSAFE_ENABLED=0 should disable safe mode on any node")
	}
}

func TestShouldUseSafeMode_MultiAgentNodes(t *testing.T) {
	// We cannot override os.Hostname() directly, but we can verify that the
	// multiAgentNodes map contains exactly the expected set of hostnames.
	expected := []string{"gaea", "nova", "sati"}
	for _, host := range expected {
		if !multiAgentNodes[host] {
			t.Errorf("multiAgentNodes should include %q", host)
		}
	}
	// Single-agent nodes must NOT be in the map
	for _, host := range []string{"prya", "vega"} {
		if multiAgentNodes[host] {
			t.Errorf("multiAgentNodes must NOT include %q", host)
		}
	}
}

func TestShouldUseSafeMode_Unset_PassThrough(t *testing.T) {
	// With GITSAFE_ENABLED unset and running on a machine that is NOT in the
	// multi-agent set, safe mode should be disabled.  We can only validate this
	// indirectly: the function must not panic and must return a bool.
	t.Setenv("GITSAFE_ENABLED", "")
	_ = shouldUseSafeMode() // must not panic
}

// ---------------------------------------------------------------------------
// isReadOnlyGitCommand
// ---------------------------------------------------------------------------

func TestIsReadOnlyGitCommand_ReadOnlyCmds(t *testing.T) {
	readonly := [][]string{
		{"status"},
		{"diff", "--stat"},
		{"log", "--oneline", "-5"},
		{"show", "HEAD"},
		{"branch", "-a"},
		{"remote", "-v"},
		{"ls-files"},
		{"ls-tree", "HEAD"},
		{"describe", "--tags"},
		{"shortlog", "-sn"},
		{"tag"},
		{"rev-parse", "HEAD"},
		{"rev-list", "HEAD"},
		{"cat-file", "-t", "HEAD"},
		{"blame", "go.mod"},
		{"annotate", "go.mod"},
		{"grep", "TODO"},
		{"bisect", "start"},
		{"stash", "list"},
		{"stash", "show"},
	}
	for _, args := range readonly {
		if !isReadOnlyGitCommand(args) {
			t.Errorf("expected read-only for %v", args)
		}
	}
}

func TestIsReadOnlyGitCommand_WriteCmds(t *testing.T) {
	write := [][]string{
		{"add", "."},
		{"commit", "-m", "msg"},
		{"reset", "--hard", "HEAD"},
		{"checkout", "main"},
		{"merge", "feature"},
		{"rebase", "main"},
		{"stash"},
		{"stash", "push"},
		{"stash", "pop"},
		{"stash", "drop"},
		{"fetch"},
		{"pull"},
		{"push"},
	}
	for _, args := range write {
		if isReadOnlyGitCommand(args) {
			t.Errorf("expected write (not read-only) for %v", args)
		}
	}
}

func TestIsReadOnlyGitCommand_Empty(t *testing.T) {
	if isReadOnlyGitCommand([]string{}) {
		t.Error("empty args should return false")
	}
}

// ---------------------------------------------------------------------------
// cleanupStaleLockIfNeeded
// ---------------------------------------------------------------------------

func TestCleanupStaleLockIfNeeded_RemovesStale(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "index.lock")

	// Create a lock file and backdate its mtime to make it stale
	if err := os.WriteFile(lockPath, []byte(""), 0o644); err != nil {
		t.Fatalf("create lock: %v", err)
	}
	staleTime := time.Now().Add(-(staleLockAgeThreshold + 5*time.Second))
	if err := os.Chtimes(lockPath, staleTime, staleTime); err != nil {
		t.Fatalf("chtimes: %v", err)
	}

	cleanupStaleLockIfNeeded(lockPath)

	if _, err := os.Stat(lockPath); !os.IsNotExist(err) {
		t.Error("stale lock should have been removed")
	}
}

func TestCleanupStaleLockIfNeeded_LeavesFresh(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "index.lock")

	// Fresh lock (just created)
	if err := os.WriteFile(lockPath, []byte(""), 0o644); err != nil {
		t.Fatalf("create lock: %v", err)
	}

	cleanupStaleLockIfNeeded(lockPath)

	if _, err := os.Stat(lockPath); err != nil {
		t.Error("fresh lock should NOT have been removed")
	}
}

func TestCleanupStaleLockIfNeeded_NoopWhenNoLock(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "index.lock")
	// Lock does not exist — must not panic
	cleanupStaleLockIfNeeded(lockPath)
}

// ---------------------------------------------------------------------------
// copyIndexWithRetry
// ---------------------------------------------------------------------------

func TestCopyIndexWithRetry_Success(t *testing.T) {
	tmpDir := t.TempDir()
	src := filepath.Join(tmpDir, "index")
	dst := filepath.Join(tmpDir, "index.copy")

	content := []byte("fake git index data")
	if err := os.WriteFile(src, content, 0o644); err != nil {
		t.Fatalf("write src: %v", err)
	}

	if err := copyIndexWithRetry(src, dst, 3); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatalf("read dst: %v", err)
	}
	if string(got) != string(content) {
		t.Errorf("content mismatch: got %q, want %q", got, content)
	}
}

func TestCopyIndexWithRetry_MissingSrc(t *testing.T) {
	tmpDir := t.TempDir()
	src := filepath.Join(tmpDir, "nonexistent-index")
	dst := filepath.Join(tmpDir, "dst")

	err := copyIndexWithRetry(src, dst, 1)
	if err == nil {
		t.Error("expected error for missing src")
	}
}

// ---------------------------------------------------------------------------
// safeCopyBack
// ---------------------------------------------------------------------------

func TestSafeCopyBack_Success(t *testing.T) {
	tmpDir := t.TempDir()
	tmpPath := filepath.Join(tmpDir, "index.tmp")
	dstPath := filepath.Join(tmpDir, "index")

	original := []byte("original index content — exactly long enough")
	if err := os.WriteFile(dstPath, original, 0o644); err != nil {
		t.Fatalf("write dst: %v", err)
	}

	// tmp has same content (size within ±50%)
	if err := os.WriteFile(tmpPath, original, 0o644); err != nil {
		t.Fatalf("write tmp: %v", err)
	}

	if err := safeCopyBack(tmpPath, dstPath); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	got, _ := os.ReadFile(dstPath)
	if string(got) != string(original) {
		t.Errorf("dst content mismatch after copy-back")
	}
}

func TestSafeCopyBack_RejectsEmpty(t *testing.T) {
	tmpDir := t.TempDir()
	tmpPath := filepath.Join(tmpDir, "index.tmp")
	dstPath := filepath.Join(tmpDir, "index")

	if err := os.WriteFile(dstPath, []byte("some original data"), 0o644); err != nil {
		t.Fatalf("write dst: %v", err)
	}
	// Write an empty tmp file
	if err := os.WriteFile(tmpPath, []byte{}, 0o644); err != nil {
		t.Fatalf("write tmp: %v", err)
	}

	if err := safeCopyBack(tmpPath, dstPath); err == nil {
		t.Error("expected error for empty tmp index")
	}
}

func TestSafeCopyBack_RejectsOversizedTmp(t *testing.T) {
	tmpDir := t.TempDir()
	tmpPath := filepath.Join(tmpDir, "index.tmp")
	dstPath := filepath.Join(tmpDir, "index")

	// dst is 10 bytes; tmp is 100 bytes (10×, exceeds 2× threshold)
	if err := os.WriteFile(dstPath, []byte("0123456789"), 0o644); err != nil {
		t.Fatalf("write dst: %v", err)
	}
	if err := os.WriteFile(tmpPath, []byte(strings.Repeat("x", 100)), 0o644); err != nil {
		t.Fatalf("write tmp: %v", err)
	}

	err := safeCopyBack(tmpPath, dstPath)
	if err == nil {
		t.Error("expected error for oversized tmp index")
	} else if !strings.Contains(err.Error(), "outside ±50%") {
		t.Errorf("error should mention ±50%%: %v", err)
	}
}

func TestSafeCopyBack_RejectsTinyTmp(t *testing.T) {
	tmpDir := t.TempDir()
	tmpPath := filepath.Join(tmpDir, "index.tmp")
	dstPath := filepath.Join(tmpDir, "index")

	// dst is 100 bytes; tmp is 1 byte (less than 50%)
	if err := os.WriteFile(dstPath, []byte(strings.Repeat("x", 100)), 0o644); err != nil {
		t.Fatalf("write dst: %v", err)
	}
	if err := os.WriteFile(tmpPath, []byte("x"), 0o644); err != nil {
		t.Fatalf("write tmp: %v", err)
	}

	if err := safeCopyBack(tmpPath, dstPath); err == nil {
		t.Error("expected error for undersized tmp index")
	}
}

func TestSafeCopyBack_MissingTmp(t *testing.T) {
	tmpDir := t.TempDir()
	tmpPath := filepath.Join(tmpDir, "index.tmp.nonexistent")
	dstPath := filepath.Join(tmpDir, "index")

	if err := os.WriteFile(dstPath, []byte("original"), 0o644); err != nil {
		t.Fatalf("write dst: %v", err)
	}

	if err := safeCopyBack(tmpPath, dstPath); err == nil {
		t.Error("expected error for missing tmp file")
	}
}

// ---------------------------------------------------------------------------
// Command registration
// ---------------------------------------------------------------------------

func TestGitAddCmd_Registered(t *testing.T) {
	if gitAddCmd.Use == "" {
		t.Error("gitAddCmd.Use should not be empty")
	}
	if gitAddCmd.RunE == nil {
		t.Error("gitAddCmd.RunE should be set")
	}
}

func TestGitRunCmd_Registered(t *testing.T) {
	if gitRunCmd.Use == "" {
		t.Error("gitRunCmd.Use should not be empty")
	}
	if gitRunCmd.RunE == nil {
		t.Error("gitRunCmd.RunE should be set")
	}
}

func TestGitCmd_HasAddAndRun(t *testing.T) {
	names := make(map[string]bool)
	for _, sub := range gitCmd.Commands() {
		names[sub.Name()] = true
	}
	for _, expected := range []string{"add", "run", "push", "guard", "commit", "clear-locks"} {
		if !names[expected] {
			t.Errorf("gitCmd should have subcommand %q", expected)
		}
	}
}

// ---------------------------------------------------------------------------
// staleLockAgeThreshold constant
// ---------------------------------------------------------------------------

func TestStaleLockAgeThreshold_IsReasonable(t *testing.T) {
	// Must be between 10s and 5 minutes — too short causes false-positives,
	// too long leaves truly stale locks blocking agents.
	if staleLockAgeThreshold < 10*time.Second {
		t.Errorf("staleLockAgeThreshold %v is too short (min 10s)", staleLockAgeThreshold)
	}
	if staleLockAgeThreshold > 5*time.Minute {
		t.Errorf("staleLockAgeThreshold %v is too long (max 5m)", staleLockAgeThreshold)
	}
}

// ---------------------------------------------------------------------------
// runWithTmpIndex integration (uses a real tmp git repo)
// ---------------------------------------------------------------------------

// initTestRepo creates a minimal git repository in a temp dir and returns its path.
func initTestRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()

	run := func(args ...string) {
		t.Helper()
		out, err := runGitCmdInDir(dir, args...)
		if err != nil {
			t.Fatalf("git %v: %v\noutput: %s", args, err, out)
		}
	}

	run("init")
	run("config", "user.email", "test@forge.local")
	run("config", "user.name", "Forge Test")

	// Create an initial commit so the index is not empty
	readme := filepath.Join(dir, "README.md")
	if err := os.WriteFile(readme, []byte("# test\n"), 0o644); err != nil {
		t.Fatalf("write README: %v", err)
	}
	run("add", "README.md")
	run("commit", "-m", "chore: initial")

	return dir
}

func TestRunWithTmpIndex_StagesFile(t *testing.T) {
	t.Setenv("GITSAFE_ENABLED", "1")

	dir := initTestRepo(t)

	// Write a new file
	newFile := filepath.Join(dir, "hello.txt")
	if err := os.WriteFile(newFile, []byte("hello forge\n"), 0o644); err != nil {
		t.Fatalf("write file: %v", err)
	}

	// Stage it via runWithTmpIndex
	if err := runWithTmpIndex(dir, []string{"add", "hello.txt"}); err != nil {
		t.Fatalf("runWithTmpIndex: %v", err)
	}

	// Verify it is staged
	staged, err := runGitCmdInDir(dir, "diff", "--cached", "--name-only")
	if err != nil {
		t.Fatalf("diff cached: %v", err)
	}
	if !strings.Contains(staged, "hello.txt") {
		t.Errorf("expected hello.txt to be staged; got: %q", staged)
	}
}

func TestRunWithTmpIndex_NotARepo(t *testing.T) {
	t.Setenv("GITSAFE_ENABLED", "1")

	// An empty temp dir is not a git repo
	dir := t.TempDir()
	err := runWithTmpIndex(dir, []string{"add", "."})
	if err == nil {
		t.Error("expected error for non-git directory")
	}
	if !strings.Contains(err.Error(), "git repository") {
		t.Errorf("error should mention git repository: %v", err)
	}
}

func TestRunWithTmpIndex_LeavesIndexClean(t *testing.T) {
	// Verify that the real index is not corrupted after a successful run.
	t.Setenv("GITSAFE_ENABLED", "1")

	dir := initTestRepo(t)

	// Capture the index size before
	indexPath := filepath.Join(dir, ".git", "index")
	infoBefore, err := os.Stat(indexPath)
	if err != nil {
		t.Fatalf("stat index before: %v", err)
	}

	// Stage a new file
	newFile := filepath.Join(dir, "canary.txt")
	if err := os.WriteFile(newFile, []byte("canary\n"), 0o644); err != nil {
		t.Fatalf("write canary: %v", err)
	}
	if err := runWithTmpIndex(dir, []string{"add", "canary.txt"}); err != nil {
		t.Fatalf("runWithTmpIndex: %v", err)
	}

	infoAfter, err := os.Stat(indexPath)
	if err != nil {
		t.Fatalf("stat index after: %v", err)
	}

	// Index size should have grown (a new entry was added)
	if infoAfter.Size() < infoBefore.Size() {
		t.Errorf("index should grow after staging: before=%d after=%d",
			infoBefore.Size(), infoAfter.Size())
	}

	// No tmp file should remain
	tmpPath := fmt.Sprintf("/tmp/forge-git-index-%d", os.Getpid())
	if _, err := os.Stat(tmpPath); !os.IsNotExist(err) {
		t.Error("tmp index file should be cleaned up after runWithTmpIndex")
		os.Remove(tmpPath)
	}
}
